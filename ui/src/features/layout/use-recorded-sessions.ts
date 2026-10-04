// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client'

import { useCallback, useEffect, useState } from 'react'
import { useChatStore } from '@/features/chat'
import { useAppConfig, useExecutionFeature, type RecordedSessionSummary } from '@/shared/context'
import { useLayoutStore } from './store'

export type RecordedSessionsStatus = 'loading' | 'ready' | 'error'

interface UseRecordedSessionsReturn {
  /** Recorded sessions of the selected pack */
  sessions: RecordedSessionSummary[]
  /** Whether the list has loaded */
  status: RecordedSessionsStatus
  /** Why the list or the last session failed to load */
  error: string | null
  /** The session being loaded, if any */
  loadingId: string | null
  /** Load a recorded session and show it as a read-only conversation */
  open: (sessionId: string) => Promise<void>
  /** List the sessions again after a failure */
  retry: () => void
}

const messageOf = (error: unknown, fallback: string): string =>
  error instanceof Error && error.message ? error.message : fallback

/**
 * The active data pack's recorded sessions, from the execution feature's
 * recordings source. Live mode lists them beside the browser's own sessions;
 * a pack without recordings simply has none there.
 */
export const useRecordedSessions = (): UseRecordedSessionsReturn => {
  const { mode } = useAppConfig()
  const { recordings } = useExecutionFeature()
  const packId = useLayoutStore((state) => state.packId)
  const [sessions, setSessions] = useState<RecordedSessionSummary[]>([])
  const [status, setStatus] = useState<RecordedSessionsStatus>('loading')
  const [error, setError] = useState<string | null>(null)
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    // Your data (the workspace) has no recordings bundle: nothing to ask for
    if (!recordings || !packId || packId === 'workspace') {
      setSessions([])
      setStatus('ready')
      return
    }
    let cancelled = false
    setStatus('loading')
    recordings
      .list(packId)
      .then((list) => {
        if (cancelled) return
        setSessions(list)
        setStatus('ready')
      })
      .catch((reason: unknown) => {
        if (cancelled) return
        setSessions([])
        // Replay mode serves the recordings only, so a missing bundle is an error there
        if (mode === 'replay') console.error('Failed to list recorded sessions:', reason)
        setError(messageOf(reason, 'Unable to load recorded sessions.'))
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [mode, recordings, attempt, packId])

  const open = useCallback(
    async (sessionId: string) => {
      if (!recordings || !packId) return
      setLoadingId(sessionId)
      try {
        useChatStore.getState().openRecordedSession(await recordings.load(packId, sessionId))
        setError(null)
      } catch (reason) {
        console.error('Failed to load recorded session:', reason)
        setError(messageOf(reason, 'Unable to load the recorded session.'))
      } finally {
        setLoadingId(null)
      }
    },
    [packId, recordings]
  )

  const retry = useCallback(() => {
    setError(null)
    setStatus('loading')
    setAttempt((count) => count + 1)
  }, [])

  return { sessions, status, error, loadingId, open, retry }
}
