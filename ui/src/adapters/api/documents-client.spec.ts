// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, test, vi } from 'vitest'
import { createDocumentsClient } from './documents-client'
import { FileInfoSchema, IngestionJobStatusSchema } from './documents-schemas'

/** A job status as the ingest service sends it: AI-Q's fields plus the pipeline's */
const STATUS = {
  job_id: 'job-1',
  status: 'processing',
  submitted_at: '2026-10-02T10:00:00Z',
  total_files: 2,
  processed_files: 1,
  collection_name: 'workspace',
  backend: 'ingest',
  metadata: {},
  file_details: [
    {
      file_id: 'f1',
      file_name: 'policy.pdf',
      status: 'ingesting',
      progress_percent: 40,
      kind: 'document',
      stage: 'parsing',
      stage_detail: 'page 1 of 2',
      parser: 'nemotron-parse-2.0',
      tables: null,
      warnings: [],
    },
    {
      file_id: 'f2',
      file_name: 'orders.csv',
      status: 'success',
      progress_percent: 100,
      kind: 'table',
      stage: 'ready',
      parser: 'duckdb-csv',
      tables: ['orders'],
    },
  ],
}

describe('documents schemas', () => {
  test('read the pipeline fields the ingest service adds', () => {
    const status = IngestionJobStatusSchema.parse(STATUS)
    expect(status.file_details[0]).toMatchObject({
      kind: 'document',
      stage: 'parsing',
      stage_detail: 'page 1 of 2',
      parser: 'nemotron-parse-2.0',
    })
    expect(status.file_details[1].tables).toEqual(['orders'])
  })

  test('accept upstream AI-Q payloads without them, and a file without chunks or metadata', () => {
    const { file_details: details, backend: _backend, metadata: _metadata, ...job } = STATUS
    const upstream = {
      ...job,
      file_details: details.map(({ file_id, file_name, status, progress_percent }) => ({
        file_id,
        file_name,
        status,
        progress_percent,
      })),
    }
    expect(IngestionJobStatusSchema.parse(upstream).file_details[0].stage).toBeUndefined()
    expect(
      FileInfoSchema.parse({
        file_id: 'f2',
        file_name: 'orders.csv',
        collection_name: 'workspace',
        status: 'success',
        kind: 'table',
        tables: ['orders'],
      })
    ).toMatchObject({ chunk_count: 0, metadata: {}, tables: ['orders'] })
  })

  test('refuse an unknown file status', () => {
    expect(() =>
      IngestionJobStatusSchema.parse({
        ...STATUS,
        file_details: [{ ...STATUS.file_details[0], status: 'parsing' }],
      })
    ).toThrow()
  })
})

describe('createDocumentsClient', () => {
  afterEach(() => vi.unstubAllGlobals())

  test('reads a job status and the workspace files through the same-origin proxy', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json(STATUS))
      .mockResolvedValueOnce(Response.json({ files: [] }))
    vi.stubGlobal('fetch', fetchMock)
    const client = createDocumentsClient()

    expect((await client.getJobStatus('job-1'))?.file_details).toHaveLength(2)
    expect(await client.listFiles('workspace')).toEqual([])
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/documents/job-1/status',
      '/api/v1/collections/workspace/documents',
    ])
  })

  test('uploads files as a multipart form, and deletes them by id', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ job_id: 'job-2', file_ids: ['f3'] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetchMock)
    const client = createDocumentsClient()

    const file = new File(['a,b\n'], 'orders.csv', { type: 'text/csv' })
    expect(await client.uploadFiles('workspace', [file])).toEqual({
      job_id: 'job-2',
      file_ids: ['f3'],
    })
    const [, uploadInit] = fetchMock.mock.calls[0]
    expect((uploadInit.body as FormData).getAll('files')).toHaveLength(1)

    await client.deleteFiles('workspace', ['f3'])
    expect(fetchMock.mock.calls[1]).toEqual([
      '/api/v1/collections/workspace/documents',
      expect.objectContaining({ method: 'DELETE', body: '{"file_ids":["f3"]}' }),
    ])
  })

  test('says why the API refused an upload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(Response.json({ detail: 'Too many files' }, { status: 413 }))
    )
    await expect(
      createDocumentsClient().uploadFiles('workspace', [new File(['x'], 'a.txt')])
    ).rejects.toThrow('Too many files')
  })
})
