// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { beforeEach, describe, expect, test } from 'vitest'
import { useDocumentsStore } from './store'
import type { FileInfo, TrackedFile } from './types'

const serverFile = (id: string, name: string, overrides: Partial<FileInfo> = {}): FileInfo => ({
  file_id: id,
  file_name: name,
  collection_name: 'workspace',
  status: 'success',
  file_size: 10,
  chunk_count: 1,
  uploaded_at: '2026-10-02',
  metadata: {},
  ...overrides,
})

const tracked = (id: string, name: string, overrides: Partial<TrackedFile> = {}): TrackedFile => ({
  id,
  fileName: name,
  fileSize: 10,
  status: 'success',
  progress: 100,
  serverFileId: id,
  collectionName: 'workspace',
  ...overrides,
})

const state = () => useDocumentsStore.getState()
const names = () => state().trackedFiles.map((file) => [file.fileName, file.status])

describe('documents store: the files the server lists', () => {
  beforeEach(() => {
    useDocumentsStore.setState({
      trackedFiles: [],
      recentlyDeletedIds: new Set(),
      loadedSessionId: null,
      filesError: null,
    })
  })

  test('replaces the collection’s files with the server’s, keeping each one’s job and upload time', () => {
    useDocumentsStore.setState({
      trackedFiles: [
        tracked('f1', 'policy.pdf', { jobId: 'job-1', uploadedAt: '2026-10-02T10:11:12Z' }),
        tracked('gone', 'old.pdf', { status: 'failed' }),
        tracked('x1', 'other.pdf', { collectionName: 'other' }),
      ],
    })
    state().setFilesFromServer('workspace', [serverFile('f1', 'policy.pdf')])
    expect(names()).toEqual([
      ['other.pdf', 'success'],
      ['policy.pdf', 'success'],
    ])
    expect(state().trackedFiles[1]).toMatchObject({
      jobId: 'job-1',
      uploadedAt: '2026-10-02T10:11:12Z',
    })
    expect(state().loadedSessionId).toBe('workspace')
  })

  test('keeps an upload, a pending delete and a refused upload the server does not list', () => {
    useDocumentsStore.setState({
      trackedFiles: [
        tracked('local-1', 'new.csv', { status: 'uploading', serverFileId: undefined }),
        tracked('f2', 'leaving.pdf', { status: 'deleting' }),
        tracked('local-2', 'huge.pdf', { status: 'failed', serverFileId: undefined }),
      ],
    })
    state().setFilesFromServer('workspace', [])
    expect(names()).toEqual([
      ['new.csv', 'uploading'],
      ['leaving.pdf', 'deleting'],
      // Refused before it reached the server: its failed card stays until removed
      ['huge.pdf', 'failed'],
    ])
  })

  test('keeps a file polling found ready that the list does not have yet, unless it was deleted', () => {
    useDocumentsStore.setState({
      trackedFiles: [tracked('f3', 'fresh.pdf'), tracked('f4', 'deleted.pdf')],
      recentlyDeletedIds: new Set(['f4']),
    })
    state().setFilesFromServer('workspace', [])
    expect(names()).toEqual([['fresh.pdf', 'success']])
  })

  test('drops a listed file just deleted here (a stale list)', () => {
    useDocumentsStore.setState({ recentlyDeletedIds: new Set(['f5']) })
    state().setFilesFromServer('workspace', [
      serverFile('f5', 'stale.pdf'),
      serverFile('f6', 'kept.pdf'),
    ])
    expect(names()).toEqual([['kept.pdf', 'success']])
  })

  test('keeps the stage a file failed at, which its list entry no longer has', () => {
    useDocumentsStore.setState({
      trackedFiles: [tracked('f7', 'scan.pdf', { status: 'ingesting', lastStage: 'embedding' })],
    })
    state().setFilesFromServer('workspace', [
      serverFile('f7', 'scan.pdf', { status: 'failed', stage: 'failed', kind: 'document' }),
    ])
    expect(state().trackedFiles[0]).toMatchObject({
      status: 'failed',
      stage: 'failed',
      lastStage: 'embedding',
    })
  })

  test('marks the files looked for, with why they could not be listed, until they are', () => {
    state().markLoaded('workspace', 'The ingest service is unavailable.')
    expect(state()).toMatchObject({
      loadedSessionId: 'workspace',
      filesError: 'The ingest service is unavailable.',
    })
    state().setFilesFromServer('workspace', [])
    expect(state().filesError).toBeNull()
  })
})
