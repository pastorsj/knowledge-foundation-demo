// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { useLayoutStore } from '@/features/layout/store'
import { POLL_INTERVAL_MS, UploadOrchestrator } from './orchestrator'
import { useDocumentsStore } from './store'
import { parserLabel, pipelineSteps } from './utils'

const getJobStatus = vi.fn()
const listFiles = vi.fn()
const getCollection = vi.fn()
vi.mock('@/adapters/api', () => ({
  createDocumentsClient: () => ({ getJobStatus, listFiles, getCollection }),
}))

/** The job's status after `polls` polls: the PDF parses, chunks, embeds; the CSV loads, profiles. */
const statusAt = (stage: string, tableStage: string, done = false) => ({
  job_id: 'job-1',
  status: done ? 'completed' : 'processing',
  submitted_at: '2026-10-02T10:00:00Z',
  total_files: 2,
  processed_files: done ? 2 : 0,
  collection_name: 'workspace',
  backend: 'ingest',
  metadata: {},
  file_details: [
    {
      file_id: 'f1',
      file_name: 'policy.pdf',
      status: stage === 'ready' ? 'success' : 'ingesting',
      progress_percent: stage === 'ready' ? 100 : 50,
      kind: 'document',
      stage,
      parser: 'nemotron-parse-2.0',
    },
    {
      file_id: 'f2',
      file_name: 'orders.csv',
      status: tableStage === 'ready' ? 'success' : 'ingesting',
      progress_percent: tableStage === 'ready' ? 100 : 50,
      kind: 'table',
      stage: tableStage,
      parser: 'duckdb-csv',
      tables: tableStage === 'ready' ? ['orders'] : null,
    },
  ],
})

const tracked = (id: string, fileName: string) => ({
  id,
  fileName,
  fileSize: 10,
  status: 'ingesting' as const,
  progress: 0,
  serverFileId: id,
  jobId: 'job-1',
  collectionName: 'workspace',
})

describe('UploadOrchestrator', () => {
  const refreshDataSources = vi.fn()

  beforeEach(() => {
    vi.useFakeTimers()
    useLayoutStore.setState({ refreshDataSources })
    useDocumentsStore.setState({
      trackedFiles: [tracked('f1', 'policy.pdf'), tracked('f2', 'orders.csv')],
      shownBannersForJobs: {},
    })
    getCollection.mockResolvedValue(null)
    listFiles.mockResolvedValue([])
  })

  afterEach(() => {
    UploadOrchestrator.cleanup()
    vi.useRealTimers()
  })

  test('polls every 1.5 s, follows each file through its stages, and refreshes the sources as files become ready', async () => {
    getJobStatus
      .mockResolvedValueOnce(statusAt('parsing', 'loading'))
      .mockResolvedValueOnce(statusAt('embedding', 'ready'))
      .mockResolvedValueOnce(statusAt('ready', 'ready', true))
    expect(POLL_INTERVAL_MS).toBe(1500)

    UploadOrchestrator.startPolling('job-1', 'workspace')
    await vi.advanceTimersByTimeAsync(0)
    const files = () => useDocumentsStore.getState().trackedFiles
    expect(files().map((file) => [file.fileName, file.stage, file.kind])).toEqual([
      ['policy.pdf', 'parsing', 'document'],
      ['orders.csv', 'loading', 'table'],
    ])
    expect(refreshDataSources).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
    expect(files()[1]).toMatchObject({ status: 'success', tables: ['orders'] })
    // The CSV's table is ready: Your tables appear before the PDF finishes
    expect(refreshDataSources).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
    expect(files()[0]).toMatchObject({ status: 'success', stage: 'ready' })
    expect(refreshDataSources).toHaveBeenCalledTimes(2)
    expect(useDocumentsStore.getState().isPolling).toBe(false)
    // The finished job opens the Files tab once
    expect(useLayoutStore.getState()).toMatchObject({
      rightPanel: 'data-sources',
      dataSourcesPanelTab: 'files',
    })
    expect(getJobStatus).toHaveBeenCalledTimes(3)
  })
})

describe('the pipeline as the card shows it', () => {
  test('steps: a document parses, chunks, embeds and indexes; a table loads and profiles', () => {
    expect(pipelineSteps('document', 'embedding', 'ingesting')).toEqual([
      { label: 'Parse', state: 'done' },
      { label: 'Chunk', state: 'done' },
      { label: 'Embed', state: 'active' },
      { label: 'Index', state: 'pending' },
    ])
    expect(pipelineSteps('table', 'profiling', 'ingesting').map((step) => step.state)).toEqual([
      'done',
      'active',
    ])
    expect(pipelineSteps('table', 'ready', 'success').map((step) => step.state)).toEqual([
      'done',
      'done',
    ])
    // A file fails at the step it stopped in, also when its stage reads `failed`
    expect(pipelineSteps('document', 'parsing', 'failed')[0].state).toBe('failed')
    expect(
      pipelineSteps('document', 'failed', 'failed', 'embedding').map((step) => step.state)
    ).toEqual(['done', 'done', 'failed', 'pending'])
    expect(pipelineSteps('table', 'failed', 'failed', 'profiling')[1].state).toBe('failed')
    // Before detection, a file is a document waiting to upload
    expect(pipelineSteps(null, null, 'uploading').map((step) => step.state)).toEqual([
      'pending',
      'pending',
      'pending',
      'pending',
    ])
  })

  test('names the parser for people', () => {
    expect(parserLabel('nemotron-parse-2.0')).toBe('Nemotron Parse 2.0')
    expect(parserLabel('pdf-text-layer')).toBe('PDF text layer')
    expect(parserLabel('docling-docx')).toBe('Docling DOCX')
    expect(parserLabel('duckdb-csv')).toBe('DuckDB CSV')
    expect(parserLabel(null)).toBeNull()
  })
})
