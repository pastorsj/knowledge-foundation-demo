// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Documents API Client
 *
 * Handles file upload, collection management, and ingestion status polling,
 * through the same-origin `/api/v1` proxy (the API forwards them to ingest).
 */

import {
  CollectionInfoSchema,
  FileInfoSchema,
  FileListResponseSchema,
  UploadResponseSchema,
  IngestionJobStatusSchema,
  type CollectionInfo,
  type FileInfo,
  type IngestionJobStatus,
} from './documents-schemas'

const getCollectionsUrl = (): string => '/api/v1/collections'

const getDocumentsBaseUrl = (): string => '/api/v1'

// ============================================================================
// Types
// ============================================================================

/** The demo has no app authentication; kept for upstream's call sites. */
export type DocumentsClientOptions = Record<string, never>

export interface UploadFilesOptions {
  /** Progress callback for XHR upload */
  onProgress?: (loaded: number, total: number) => void
  /** AbortSignal for cancellation */
  signal?: AbortSignal
}

// ============================================================================
// Helpers
// ============================================================================

/**
 * Parse API error response and throw a consistent error
 */
async function handleApiError(response: Response, context: string): Promise<never> {
  const error = await response.json().catch(() => ({}))
  const detail = typeof error?.detail === 'string' ? error.detail : null
  throw new Error(error?.error?.message || detail || `${context}: ${response.statusText}`)
}

// ============================================================================
// Client Factory
// ============================================================================

/**
 * Create a documents API client
 *
 * @param options - Client options (none)
 * @returns Documents client with all API methods
 *
 * The ingest service creates Your data's one collection, `workspace`, at startup: the client
 * reads it, and uploads to it, but never creates or deletes a collection.
 *
 * @example
 * ```typescript
 * const client = createDocumentsClient()
 *
 * // Upload files
 * const { job_id, file_ids } = await client.uploadFiles('workspace', files)
 *
 * // Poll for status
 * const status = await client.getJobStatus(job_id)
 * ```
 */
export const createDocumentsClient = (_options: DocumentsClientOptions = {}) => {
  // Helper to create headers
  const getHeaders = (includeContentType = true): Record<string, string> => {
    const headers: Record<string, string> = {}
    if (includeContentType) {
      headers['Content-Type'] = 'application/json'
    }
    return headers
  }

  return {
    // --------------------------------------------------------------------------
    // Collection Management
    // --------------------------------------------------------------------------

    /**
     * Get a specific collection by name
     */
    async getCollection(name: string, signal?: AbortSignal): Promise<CollectionInfo | null> {
      const response = await fetch(`${getCollectionsUrl()}/${name}`, {
        method: 'GET',
        headers: getHeaders(),
        signal,
      })

      if (response.status === 404) {
        return null
      }

      if (!response.ok) {
        await handleApiError(response, 'Failed to get collection')
      }

      const data = await response.json()
      return CollectionInfoSchema.parse(data)
    },

    // --------------------------------------------------------------------------
    // File Operations
    // --------------------------------------------------------------------------

    /**
     * Upload files to a collection
     *
     * @param collectionName - Target collection name
     * @param files - Files to upload
     * @param options - Upload options (progress callback)
     * @returns Job ID and file IDs for status polling
     */
    async uploadFiles(
      collectionName: string,
      files: File[],
      options?: UploadFilesOptions
    ): Promise<{ job_id: string; file_ids: string[] }> {
      const formData = new FormData()
      files.forEach((file) => {
        formData.append('files', file)
      })

      // Use XMLHttpRequest for progress tracking if callback provided
      if (options?.onProgress) {
        return new Promise((resolve, reject) => {
          const xhr = new XMLHttpRequest()

          // Handle abort signal
          if (options.signal) {
            if (options.signal.aborted) {
              reject(new DOMException('Upload aborted', 'AbortError'))
              return
            }
            options.signal.addEventListener('abort', () => {
              xhr.abort()
              reject(new DOMException('Upload aborted', 'AbortError'))
            })
          }

          xhr.upload.addEventListener('progress', (event) => {
            if (event.lengthComputable) {
              options.onProgress?.(event.loaded, event.total)
            }
          })

          xhr.addEventListener('load', () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              try {
                const data = JSON.parse(xhr.responseText)
                const validated = UploadResponseSchema.parse(data)
                resolve({
                  job_id: validated.job_id,
                  file_ids: validated.file_ids,
                })
              } catch (error) {
                reject(new Error(`Failed to parse upload response: ${error}`))
              }
            } else {
              reject(new Error(`Upload failed: ${xhr.statusText}`))
            }
          })

          xhr.addEventListener('error', () => {
            reject(new Error('Upload failed: Network error'))
          })

          xhr.open('POST', `${getCollectionsUrl()}/${collectionName}/documents`)
          xhr.send(formData)
        })
      }

      // Standard fetch for simple uploads
      // Don't set Content-Type for FormData - browser sets it with boundary
      const response = await fetch(`${getCollectionsUrl()}/${collectionName}/documents`, {
        method: 'POST',
        body: formData,
        signal: options?.signal,
      })

      if (!response.ok) {
        await handleApiError(response, 'Failed to upload files')
      }

      const data = await response.json()
      const validated = UploadResponseSchema.parse(data)
      return {
        job_id: validated.job_id,
        file_ids: validated.file_ids,
      }
    },

    /**
     * List files in a collection
     */
    async listFiles(collectionName: string, signal?: AbortSignal): Promise<FileInfo[]> {
      const response = await fetch(`${getCollectionsUrl()}/${collectionName}/documents`, {
        method: 'GET',
        headers: getHeaders(),
        signal,
      })

      if (!response.ok) {
        await handleApiError(response, 'Failed to list files')
      }

      const data = await response.json()

      // API might return array directly OR wrapped in {files: [...]}
      // Handle both cases
      if (Array.isArray(data)) {
        // Validate each file individually
        return data.map((file) => FileInfoSchema.parse(file))
      }

      const validated = FileListResponseSchema.parse(data)
      return validated.files
    },

    /**
     * Delete files from a collection
     */
    async deleteFiles(collectionName: string, fileIds: string[]): Promise<void> {
      const response = await fetch(`${getCollectionsUrl()}/${collectionName}/documents`, {
        method: 'DELETE',
        headers: getHeaders(),
        body: JSON.stringify({ file_ids: fileIds }),
      })

      if (!response.ok && response.status !== 404) {
        await handleApiError(response, 'Failed to delete files')
      }
    },

    // --------------------------------------------------------------------------
    // Job Status (Polling)
    // --------------------------------------------------------------------------

    /**
     * Get ingestion job status
     *
     * @param jobId - Job ID from uploadFiles response
     * @param signal - Optional AbortSignal for cancellation
     * @returns Job status with file progress details
     */
    async getJobStatus(jobId: string, signal?: AbortSignal): Promise<IngestionJobStatus | null> {
      const response = await fetch(`${getDocumentsBaseUrl()}/documents/${jobId}/status`, {
        method: 'GET',
        headers: getHeaders(),
        signal,
      })

      if (response.status === 404) {
        return null
      }

      if (!response.ok) {
        await handleApiError(response, 'Failed to get job status')
      }

      const data = await response.json()
      return IngestionJobStatusSchema.parse(data)
    },
  }
}

export type DocumentsClient = ReturnType<typeof createDocumentsClient>
