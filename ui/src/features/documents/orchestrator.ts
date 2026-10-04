// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Upload Orchestrator
 *
 * Centralized service for managing file upload orchestration including:
 * - Polling lifecycle (start, stop, cleanup)
 * - Persistence (localStorage save/restore for page refresh)
 * - Session switching (cleanup, resume)
 * - File loading from server
 *
 * This service lives outside React's lifecycle to avoid complex ref coordination
 * and effect races that were previously managed in the useFileUpload hook.
 *
 * Here the "session" is always the workspace collection (`workspace`), active
 * while Your data is the selected pack. Polling runs every 1.5 s while a job
 * is active, and each file that becomes ready refreshes the layout's data
 * sources, so its documents and tables can be asked about at once.
 */

import { createDocumentsClient } from '@/adapters/api'
import { useDocumentsStore } from './store'
import { useLayoutStore } from '@/features/layout/store'
import type { TrackedFile } from './types'
import { mapBackendStatus } from './utils'
import {
  persistJob,
  removePersistedJob,
  getPersistedJobForCollection,
  updatePersistedJobFiles,
  markSessionHasCollection,
  unmarkSessionCollection,
} from './persistence'

export const POLL_INTERVAL_MS = 1500
/** About 35 minutes of polling, as upstream's 420 polls at 5 s */
const MAX_POLL_ATTEMPTS = 1400

interface PollingState {
  jobId: string
  collectionName: string
  timeoutId: NodeJS.Timeout | null
  pollCount: number
  abortController: AbortController
}

interface OrchestratorCallbacks {
  onComplete?: () => void
  onError?: (error: Error) => void
}

class UploadOrchestratorImpl {
  private pollingState: PollingState | null = null
  private currentSessionId: string | null = null
  private lastLoadedSessionId: string | null = null
  private callbacks: OrchestratorCallbacks = {}
  /** Files known to be ready, so each refreshes the data sources once */
  private readyFiles = new Set<string>()

  setCallbacks(callbacks: OrchestratorCallbacks): void {
    this.callbacks = callbacks
  }

  private getClient() {
    return createDocumentsClient()
  }

  private getStore() {
    return useDocumentsStore.getState()
  }

  /**
   * Handle session change - cleans up previous session and sets up new one
   */
  async handleSessionChange(newSessionId: string | undefined): Promise<void> {
    const previousSessionId = this.currentSessionId

    if (newSessionId === previousSessionId) {
      return
    }

    // Stop polling from previous session
    this.stopPolling()

    // Clear any upload error from previous session
    this.getStore().clearError()

    // Clear files for old session
    if (previousSessionId) {
      this.getStore().clearFilesForCollection(previousSessionId)
    }

    // Update session ID immediately to prevent race conditions
    this.currentSessionId = newSessionId ?? null
    this.lastLoadedSessionId = null

    // Signal loading immediately so the UI shows a spinner before any async work.
    // loadFilesForSession (or early returns below) will clear this.
    if (newSessionId) {
      this.getStore().setLoadingFiles(true)
    }

    // Check for persisted job to resume
    if (newSessionId) {
      const persistedJob = getPersistedJobForCollection(newSessionId)
      if (persistedJob) {
        // Verify session hasn't changed during sync operations
        if (this.currentSessionId === newSessionId) {
          await this.resumeFromPersistence(newSessionId, persistedJob.jobId)
        }
        return
      }
    }

    // Load files from server for new session
    if (newSessionId && this.currentSessionId === newSessionId) {
      await this.loadFilesForSession(newSessionId)
    }
  }

  /**
   * Load files from server for a session.
   * Only queries the backend if the session is known to have a collection
   * (from a previous upload) or has a persisted job in progress.
   * This prevents unnecessary 404 errors for sessions that never had files uploaded.
   */
  async loadFilesForSession(sessionId: string): Promise<void> {
    const store = this.getStore()

    if (sessionId === this.lastLoadedSessionId) {
      store.setLoadingFiles(false)
      return
    }

    if (store.isUploading || store.isPolling) {
      store.setLoadingFiles(false)
      return
    }

    // Check if session changed before making network request
    if (sessionId !== this.currentSessionId) {
      store.setLoadingFiles(false)
      return
    }

    // The workspace collection is shared by every session and browser: always ask for its files
    const client = this.getClient()

    store.setLoadingFiles(true)
    try {
      const collection = await client.getCollection(sessionId)

      // Re-check session after async call to avoid stale updates
      if (sessionId !== this.currentSessionId) {
        return
      }

      if (collection) {
        const files = await client.listFiles(sessionId)

        // Final session check after second async call
        if (sessionId !== this.currentSessionId) {
          return
        }

        store.setFilesFromServer(sessionId, files)
        store.setCurrentCollection(sessionId)
        store.setCollectionInfo(collection)

        // Confirm the collection marker (in case it was set by a different mechanism)
        markSessionHasCollection(sessionId)
      } else {
        // No workspace collection (404): the ingest service creates it when it starts, so it is
        // starting, or its store was reset. Say so; the Files tab offers to try again.
        unmarkSessionCollection(sessionId)
        store.markLoaded(
          sessionId,
          'The ingestion service has not created Your data’s collection yet. It does when it starts.'
        )
      }

      this.lastLoadedSessionId = sessionId
    } catch (error) {
      // The ingest service or the API is down (a 502, 503 or 504, or no answer): say so, and
      // don't retry by itself (the Files tab offers to). The collection marker stays.
      this.lastLoadedSessionId = sessionId
      if (sessionId === this.currentSessionId) {
        store.markLoaded(
          sessionId,
          error instanceof Error && error.message ? error.message : 'The request failed.'
        )
      }
    } finally {
      store.setLoadingFiles(false)
    }
  }

  /**
   * Resume polling from a persisted job (after page refresh)
   */
  private async resumeFromPersistence(sessionId: string, jobId: string): Promise<void> {
    const store = this.getStore()
    const client = this.getClient()
    const persistedJob = getPersistedJobForCollection(sessionId)

    if (!persistedJob) return

    // Open files panel to show progress
    const layoutStore = useLayoutStore.getState()
    layoutStore.openRightPanel('data-sources')
    layoutStore.setDataSourcesPanelTab('files')

    try {
      const [serverFiles, jobStatus] = await Promise.all([
        client.listFiles(sessionId).catch(() => []),
        client.getJobStatus(jobId).catch(() => null),
      ])

      if (serverFiles.length > 0) {
        store.setFilesFromServer(sessionId, serverFiles)
      }

      if (jobStatus && jobStatus.status !== 'completed' && jobStatus.status !== 'failed') {
        const serverFileNames = new Set(serverFiles.map((f) => f.file_name))
        for (const jobFile of jobStatus.file_details) {
          if (!serverFileNames.has(jobFile.file_name)) {
            const persistedFile = persistedJob.files.find((f) => f.fileName === jobFile.file_name)
            if (persistedFile) {
              store.addTrackedFile({
                ...persistedFile,
                status: mapBackendStatus(jobFile.status),
                progress: jobFile.progress_percent,
                errorMessage: jobFile.error_message ?? undefined,
                serverFileId: jobFile.file_id,
              } as TrackedFile)
            }
          }
        }
      }

      if (serverFiles.length === 0 && (!jobStatus || jobStatus.file_details.length === 0)) {
        for (const file of persistedJob.files) {
          store.addTrackedFile(file as TrackedFile)
        }
      }
    } catch {
      for (const file of persistedJob.files) {
        store.addTrackedFile(file as TrackedFile)
      }
    } finally {
      // The files are listed (or the persisted ones stand in for them): the tab is no longer loading
      this.lastLoadedSessionId = sessionId
      store.markLoaded(sessionId)
      store.setLoadingFiles(false)
    }

    this.startPolling(jobId, sessionId)
  }

  /**
   * Start polling for a job
   */
  startPolling(jobId: string, collectionName: string, filesToPersist?: TrackedFile[]): void {
    this.stopPolling()

    const store = this.getStore()
    store.setPolling(true)
    store.setActiveJobId(jobId)

    if (filesToPersist && filesToPersist.length > 0) {
      persistJob(jobId, collectionName, filesToPersist)
    }

    const abortController = new AbortController()

    this.pollingState = {
      jobId,
      collectionName,
      timeoutId: null,
      pollCount: 0,
      abortController,
    }

    this.pollJobStatus()
  }

  /**
   * Stop current polling
   */
  stopPolling(): void {
    if (this.pollingState) {
      if (this.pollingState.timeoutId) {
        clearTimeout(this.pollingState.timeoutId)
      }
      this.pollingState.abortController.abort()
      this.pollingState = null
    }

    const store = this.getStore()
    store.setPolling(false)
    store.setActiveJobId(null)
  }

  /**
   * Stop polling only if it targets the given collection (session id).
   * Used when removing an upload-only session without affecting other sessions.
   */
  stopPollingIfCollection(collectionName: string): void {
    if (this.pollingState?.collectionName === collectionName) {
      this.stopPolling()
    }
  }

  /**
   * Poll job status
   */
  private async pollJobStatus(): Promise<void> {
    if (!this.pollingState) return

    const { jobId, collectionName, abortController } = this.pollingState
    const store = this.getStore()
    const client = this.getClient()

    if (abortController.signal.aborted) {
      this.stopPolling()
      return
    }

    if (this.pollingState.pollCount >= MAX_POLL_ATTEMPTS) {
      this.stopPolling()
      store.setError('Upload timed out. Please try again.')
      removePersistedJob(jobId)
      return
    }

    try {
      const status = await client.getJobStatus(jobId, abortController.signal)

      if (!status) {
        this.stopPolling()
        store.setError('Job not found')
        removePersistedJob(jobId)
        return
      }

      store.updateFilesFromJobStatus(status)
      this.refreshWhenReady()

      const currentFiles = store.trackedFiles.filter((f) => f.jobId === jobId)
      if (currentFiles.length > 0) {
        updatePersistedJobFiles(jobId, currentFiles)
      }

      const isTerminal = status.status === 'completed' || status.status === 'failed'

      if (isTerminal) {
        this.stopPolling()
        removePersistedJob(jobId)

        // Open Data Sources panel for any terminal state so the user
        // can see available files or errors
        const docStore = useDocumentsStore.getState()
        const layoutStore = useLayoutStore.getState()
        const jobBanners = docStore.shownBannersForJobs[jobId]
        if (!jobBanners?.ingested) {
          layoutStore.setDataSourcesPanelTab('files')
          layoutStore.openRightPanel('data-sources')
          docStore.markBannerShown(jobId, 'ingested')
        }

        if (status.status === 'completed') {
          this.callbacks.onComplete?.()
        } else if (status.error_message) {
          store.setError(status.error_message)
          this.callbacks.onError?.(new Error(status.error_message))
        }

        // Reload files from server (setFilesFromServer replaces files for the
        // collection while preserving client-side metadata like uploadedAt)
        this.lastLoadedSessionId = null
        this.loadFilesForSession(collectionName)
        return
      }

      this.pollingState.pollCount++
      this.scheduleNextPoll()
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        this.stopPolling()
        return
      }

      // Log polling errors for debugging
      console.error('[UploadOrchestrator] Poll error:', err)

      if (!this.pollingState) return

      this.pollingState.pollCount++

      if (this.pollingState.pollCount >= MAX_POLL_ATTEMPTS) {
        this.stopPolling()
        const message = err instanceof Error ? err.message : 'Polling failed'
        store.setError(message)
        this.callbacks.onError?.(err instanceof Error ? err : new Error(message))
      } else {
        this.scheduleNextPoll()
      }
    }
  }

  /**
   * Refreshes the layout's data sources when a file became ready: a document is then retrievable,
   * a table's source appears (an empty `workspace.tables` is hidden until its first table lands).
   */
  private refreshWhenReady(): void {
    const ready = this.getStore()
      .trackedFiles.filter((file) => file.status === 'success')
      .map((file) => file.serverFileId ?? file.id)
      .filter((id) => !this.readyFiles.has(id))
    if (!ready.length) return
    ready.forEach((id) => this.readyFiles.add(id))
    void useLayoutStore.getState().refreshDataSources()
  }

  /**
   * Schedule the next poll using setTimeout
   */
  private scheduleNextPoll(): void {
    if (!this.pollingState) return

    this.pollingState.timeoutId = setTimeout(() => {
      this.pollJobStatus()
    }, POLL_INTERVAL_MS)
  }

  /**
   * Force-refresh files for the current session from the backend.
   * Bypasses the lastLoadedSessionId cache so the backend is always queried.
   * Use when the UI needs to reconcile with possible backend-side changes
   * (e.g. TTL-based collection cleanup).
   */
  async refreshFilesForSession(sessionId: string): Promise<void> {
    if (sessionId === this.currentSessionId) {
      this.lastLoadedSessionId = null
      await this.loadFilesForSession(sessionId)
    }
  }

  /**
   * Cleanup on unmount
   */
  cleanup(): void {
    this.stopPolling()
    this.currentSessionId = null
    this.lastLoadedSessionId = null
  }
}

export const UploadOrchestrator = new UploadOrchestratorImpl()
