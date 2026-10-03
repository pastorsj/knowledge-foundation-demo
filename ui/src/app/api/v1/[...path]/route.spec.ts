// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { GET, POST } from './route'

const call = (handler: typeof GET, path: string, init?: RequestInit) =>
  handler(new Request(`http://ui.test/api/v1/${path}`, init), {
    params: Promise.resolve({ path: path.split('?')[0].split('/') }),
  })

describe('/api/v1 proxy', () => {
  const upstream = vi.fn()

  beforeEach(() => {
    vi.stubEnv('API_URL', 'http://api.test:8000')
    upstream.mockResolvedValue(Response.json({ ok: true }))
    vi.stubGlobal('fetch', upstream)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  test.each([
    ['GET', 'pack'],
    ['GET', 'data_sources'],
    ['GET', 'data_sources/retail.sales/preview?table=orders&limit=5'],
    ['POST', 'data_sources/retail.sales/query'],
    ['POST', 'jobs/async/submit'],
    ['GET', 'jobs/async/job/job-1'],
    ['GET', 'jobs/async/job/job-1/stream/42'],
    ['POST', 'jobs/async/job/job-1/cancel'],
  ])('forwards %s /v1/%s', async (method, path) => {
    const response = await call(method === 'GET' ? GET : POST, path, {
      method,
      ...(method === 'POST' && { body: '{}' }),
    })

    expect(response.status).toBe(200)
    expect(upstream).toHaveBeenCalledWith(`http://api.test:8000/v1/${path}`, expect.anything())
  })

  test.each([
    ['GET', 'internal/hermes/jobs/job-1/execution-scope'],
    ['POST', 'data_sources'],
    ['POST', 'jobs/async/job/job-1/report'],
    // The market demo's CPU/GPU benchmark is gone
    ['POST', 'jobs/async/job/job-1/benchmark'],
    ['GET', 'jobs/async/job/../../internal'],
    ['GET', 'jobs/async/job/.hidden'],
  ])('rejects %s /v1/%s without calling the API', async (method, path) => {
    const response = await call(method === 'GET' ? GET : POST, path, { method })

    expect(response.status).toBe(404)
    expect(upstream).not.toHaveBeenCalled()
  })

  test('forwards only the allowed headers', async () => {
    await call(POST, 'jobs/async/submit', {
      method: 'POST',
      body: '{"input":"q"}',
      headers: {
        'content-type': 'application/json',
        'conversation-id': 's_1',
        cookie: 'secret=1',
      },
    })

    const init = upstream.mock.calls[0][1] as RequestInit
    const headers = init.headers as Headers
    expect(headers.get('conversation-id')).toBe('s_1')
    expect(headers.get('cookie')).toBeNull()
    expect(init.body).toBe('{"input":"q"}')
  })

  test('forwards a voice recording as bytes and refuses anything but a bounded WAV', async () => {
    const wav = new Uint8Array([82, 73, 70, 70, 0, 255, 128])
    const audio = { method: 'POST', headers: { 'content-type': 'audio/wav' } }

    const response = await call(POST, 'speech/transcriptions', { ...audio, body: wav })
    const text = await call(POST, 'speech/transcriptions', { method: 'POST', body: 'hello' })
    const large = await call(POST, 'speech/transcriptions', {
      ...audio,
      body: new Uint8Array(3 * 1024 * 1024 + 1),
    })

    expect(response.status).toBe(200)
    const init = upstream.mock.calls[0][1] as RequestInit
    expect(new Uint8Array(init.body as ArrayBuffer)).toEqual(wav)
    expect((init.headers as Headers).get('content-type')).toBe('audio/wav')
    expect([text.status, large.status]).toEqual([415, 413])
    expect(upstream).toHaveBeenCalledOnce()
  })

  test('passes event streams through unbuffered', async () => {
    upstream.mockResolvedValue(
      new Response('event: job.status\ndata: {}\n\n', {
        headers: { 'content-type': 'text/event-stream' },
      })
    )

    const response = await call(GET, 'jobs/async/job/job-1/stream')

    expect(response.headers.get('content-type')).toBe('text/event-stream')
    expect(response.headers.get('x-accel-buffering')).toBe('no')
    expect(await response.text()).toContain('event: job.status')
  })

  test('answers 502 when the API is unreachable', async () => {
    upstream.mockRejectedValue(new TypeError('fetch failed'))

    const response = await call(GET, 'pack')

    expect(response.status).toBe(502)
  })

  test('never calls the API in replay mode', async () => {
    vi.stubEnv('UI_MODE', 'replay')

    const response = await call(GET, 'pack')

    expect(response.status).toBe(404)
    expect(upstream).not.toHaveBeenCalled()
  })
})
