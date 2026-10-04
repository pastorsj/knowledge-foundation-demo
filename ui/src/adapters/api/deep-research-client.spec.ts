// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { FakeEventSource } from '@/test-utils/fake-event-source'
import {
  ApiRequestError,
  cancelJob,
  createDeepResearchClient,
  submitJob,
} from './deep-research-client'

describe('createDeepResearchClient', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  const connect = (callbacks = {}, lastEventId?: string) => {
    const client = createDeepResearchClient({ jobId: 'job-1', callbacks, lastEventId })
    client.connect()
    return { client, source: FakeEventSource.latest }
  }

  test('streams the job through the same-origin proxy, resuming after an event ID', () => {
    expect(connect().source.url).toBe('/api/v1/jobs/async/job/job-1/stream')
    expect(connect({}, '42').source.url).toBe('/api/v1/jobs/async/job/job-1/stream/42')
  })

  test('forwards every record, with its cursor, before the typed callbacks', () => {
    const onEvent = vi.fn()
    const { source } = connect({ onEvent })

    source.emit('execution.v2', { schemaVersion: '2' }, '7')

    expect(onEvent).toHaveBeenCalledWith({
      jobId: 'job-1',
      cursor: '7',
      type: 'execution.v2',
      data: { schemaVersion: '2' },
    })
  })

  test('reports the final answer from the final_report output artifact', () => {
    const onFinalReport = vi.fn()
    const { source } = connect({ onFinalReport })

    source.emit('artifact.update', {
      data: { type: 'output', output_category: 'draft', content: 'x' },
    })
    source.emit('artifact.update', {
      data: { type: 'output', output_category: 'final_report', content: '# Answer' },
    })

    expect(onFinalReport).toHaveBeenCalledOnce()
    expect(onFinalReport).toHaveBeenCalledWith('# Answer')
  })

  test('closes the stream on a terminal job status', () => {
    const onJobStatus = vi.fn()
    const { source } = connect({ onJobStatus })

    source.emit('job.status', { data: { status: 'running' } })
    expect(source.readyState).not.toBe(FakeEventSource.CLOSED)

    source.emit('job.status', { status: 'failure', error: 'model unavailable' })
    expect(onJobStatus).toHaveBeenLastCalledWith('failure', 'model unavailable')
    expect(source.readyState).toBe(FakeEventSource.CLOSED)
  })

  test('keeps the stream alive through a failing consumer', () => {
    const onHeartbeat = vi.fn()
    const { source } = connect({
      onEvent: () => {
        throw new Error('consumer bug')
      },
      onHeartbeat,
    })
    vi.spyOn(console, 'warn').mockImplementation(() => {})

    source.emit('job.heartbeat', {})

    expect(onHeartbeat).toHaveBeenCalledOnce()
  })

  test('surfaces an error only after repeated reconnection failures', () => {
    const onError = vi.fn()
    const { source } = connect({ onError })

    for (let attempt = 0; attempt < 5; attempt++) source.fail(FakeEventSource.CONNECTING)
    expect(onError).not.toHaveBeenCalled()

    source.fail(FakeEventSource.CONNECTING)
    expect(onError).toHaveBeenCalledOnce()
  })

  test('reports a disconnect when the browser gives up', () => {
    const onDisconnect = vi.fn()
    const { source } = connect({ onDisconnect })

    source.fail(FakeEventSource.CLOSED)

    expect(onDisconnect).toHaveBeenCalledOnce()
  })
})

describe('REST functions', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('submits a job with its caller-chosen ID and conversation', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(Response.json({ job_id: 'job-1', status: 'submitted' }))
    vi.stubGlobal('fetch', fetchMock)

    await submitJob({
      input: 'Which stores led in Q3?',
      conversationId: 's_1',
      dataSources: ['retail.policies'],
      jobId: 'job-1',
    })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/jobs/async/submit')
    expect(init.method).toBe('POST')
    expect(init.headers).toMatchObject({ 'conversation-id': 's_1' })
    expect(JSON.parse(init.body)).toEqual({
      agent_type: 'hermes',
      input: 'Which stores led in Q3?',
      data_sources: ['retail.policies'],
      job_id: 'job-1',
    })
  })

  test('includes the API error detail in failures', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(Response.json({ detail: 'Unknown source' }, { status: 422 }))
    )

    await expect(cancelJob('job-1')).rejects.toThrow('Failed to cancel job: 422 - Unknown source')
  })

  test('tells an answer from the API apart from one by the UI proxy', async () => {
    const submit = () =>
      submitJob({
        input: 'Which stores led in Q3?',
        conversationId: 's_1',
        dataSources: [],
        jobId: 'job-1',
      })
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ detail: 'The API is starting or stopping.' }, { status: 503 })
      )
      .mockResolvedValueOnce(
        Response.json(
          { error: { code: 'PROXY_ERROR', message: 'The API is unavailable' } },
          { status: 502 }
        )
      )
    vi.stubGlobal('fetch', fetchMock)

    const refused = await submit().catch((error: unknown) => error)
    const lost = await submit().catch((error: unknown) => error)

    expect(refused).toBeInstanceOf(ApiRequestError)
    expect(refused).toMatchObject({
      status: 503,
      fromApi: true,
      message: 'Failed to start research: 503 - The API is starting or stopping.',
    })
    expect(lost).toMatchObject({
      status: 502,
      fromApi: false,
      message: 'Failed to start research: 502 - The API is unavailable',
    })
  })
})
