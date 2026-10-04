// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { getFileUploadConfigFromEnv } from '@/shared/config/file-upload'
import { AppConfigProvider } from '@/shared/context'
import { UploadOrchestrator } from '../orchestrator'
import { useDocumentsStore } from '../store'
import { useFileUpload } from './use-file-upload'

const wrapper = ({ children }: { children: ReactNode }) => (
  <AppConfigProvider
    config={{
      mode: 'live',
      defaultPack: 'retail',
      phoenixUrl: null,
      speechInput: { enabled: false, maxSeconds: 60 },
      fileUpload: getFileUploadConfigFromEnv({} as NodeJS.ProcessEnv),
    }}
  >
    {children}
  </AppConfigProvider>
)

const COLLECTION = { name: 'workspace', file_count: 0, chunk_count: 0, metadata: {} }

/** The API: the workspace collection, no files, and `upload` for an upload */
const stubApi = (upload: () => Response) =>
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'POST') return upload()
      return Response.json(url.endsWith('/documents') ? { files: [] } : COLLECTION)
    })
  )

describe('useFileUpload', () => {
  beforeEach(() => {
    UploadOrchestrator.cleanup()
    useDocumentsStore.setState({
      trackedFiles: [],
      error: null,
      isUploading: false,
      loadedSessionId: null,
    })
  })

  afterEach(() => {
    UploadOrchestrator.cleanup()
    vi.unstubAllGlobals()
  })

  test.each([
    [
      413,
      {
        error: {
          code: 'UPLOAD_TOO_LARGE',
          message: 'The upload is larger than this deployment accepts',
        },
      },
      'The upload is larger than this deployment accepts',
    ],
    [415, { detail: 'Expected a multipart upload' }, 'Expected a multipart upload'],
  ])(
    'a %s fails the upload’s files with the reason the server gives',
    async (status, body, reason) => {
      stubApi(() => Response.json(body, { status }))
      const { result } = renderHook(() => useFileUpload({ sessionId: 'workspace' }), { wrapper })

      await act(() =>
        result.current.uploadFiles([new File(['a,b\n1,2\n'], 'orders.csv', { type: 'text/csv' })])
      )

      await waitFor(() => expect(result.current.error).toBe(reason))
      expect(result.current.sessionFiles).toEqual([
        expect.objectContaining({ fileName: 'orders.csv', status: 'failed', errorMessage: reason }),
      ])
      expect(result.current.isUploading).toBe(false)
    }
  )

  test('does not send a file the ingest service would refuse', async () => {
    const upload = vi.fn(() => Response.json({ job_id: 'j', file_ids: [] }))
    stubApi(upload)
    const { result } = renderHook(() => useFileUpload({ sessionId: 'workspace' }), { wrapper })

    await act(() => result.current.uploadFiles([new File(['MZ'], 'setup.exe')]))

    expect(result.current.error).toMatch(/"setup.exe" is not a supported file type/)
    expect(upload).not.toHaveBeenCalled()
  })
})
