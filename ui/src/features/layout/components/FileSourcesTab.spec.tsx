// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { UploadOrchestrator, useDocumentsStore } from '@/features/documents'
import { render, screen } from '@/test-utils'
import { FileSourcesTab } from './FileSourcesTab'

const getCollection = vi.fn()
const listFiles = vi.fn()
vi.mock('@/adapters/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/adapters/api')>()),
  createDocumentsClient: () => ({ getCollection, listFiles, getJobStatus: vi.fn() }),
}))

const COLLECTION = { name: 'workspace', file_count: 0, chunk_count: 0, metadata: {} }

describe('FileSourcesTab', () => {
  beforeEach(() => {
    UploadOrchestrator.cleanup()
    useDocumentsStore.setState({
      trackedFiles: [],
      loadedSessionId: null,
      filesError: null,
      isLoadingFiles: false,
      isUploading: false,
      isPolling: false,
      error: null,
    })
    getCollection.mockReset()
    listFiles.mockReset().mockResolvedValue([])
  })

  afterEach(() => UploadOrchestrator.cleanup())

  test('says it is checking for files while they load', () => {
    getCollection.mockReturnValue(new Promise(() => undefined))
    render(<FileSourcesTab />)
    expect(screen.getByText('Checking for files...')).toBeVisible()
  })

  test('with no files, explains Your data and offers the upload zone', async () => {
    getCollection.mockResolvedValue(COLLECTION)
    render(<FileSourcesTab />)
    expect(await screen.findByText('No Attached Files')).toBeVisible()
    expect(screen.getByTestId('accepted-types')).toBeVisible()
    expect(screen.queryByTestId('files-error')).toBeNull()
  })

  test('when the ingest service is down, says so with a Retry, and still offers the upload zone', async () => {
    getCollection.mockRejectedValueOnce(new Error('The ingest service is unavailable.'))
    render(<FileSourcesTab />)

    const banner = await screen.findByTestId('files-error')
    expect(banner).toHaveTextContent('Couldn’t reach the ingestion service')
    expect(banner).toHaveTextContent('The ingest service is unavailable.')
    expect(screen.queryByText('Checking for files...')).toBeNull()
    expect(screen.getByTestId('accepted-types')).toBeVisible()

    getCollection.mockResolvedValue(COLLECTION)
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('No Attached Files')).toBeVisible()
    expect(screen.queryByTestId('files-error')).toBeNull()
  })
})
