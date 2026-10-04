// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { DELETE, GET, POST } from './route'

/**
 * A request as the server sees it: fetch's Request drops the forbidden headers a browser sets itself
 * (Origin, Sec-Fetch-*), so the test puts them back the way Node's incoming request carries them.
 */
const withHeaders = (request: Request, headers?: HeadersInit): Request => {
  if (headers) {
    const all = new Headers(request.headers)
    for (const [name, value] of new Headers(headers)) all.set(name, value)
    Object.defineProperty(request, 'headers', { value: all })
  }
  return request
}

const call = (handler: typeof GET, path: string, init?: RequestInit) =>
  handler(withHeaders(new Request(`http://ui.test/api/v1/${path}`, init), init?.headers), {
    params: Promise.resolve({ path: path.split('/') }),
  })

const UPLOAD_PATH = ['collections', 'workspace', 'documents']

const upload = (body: FormData) =>
  POST(
    new Request('http://ui.test/api/v1/collections/workspace/documents', { method: 'POST', body }),
    { params: Promise.resolve({ path: UPLOAD_PATH }) }
  )

describe('/api/v1 proxy: Your data', () => {
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
    ['GET', 'packs'],
    ['GET', 'collections/workspace'],
    ['GET', 'collections/workspace/documents'],
    ['GET', 'documents/job-1/status'],
  ])('forwards %s /v1/%s', async (method, path) => {
    const handler = method === 'GET' ? GET : method === 'POST' ? POST : DELETE
    const response = await call(handler, path, {
      method,
      ...(method === 'POST' && { body: '{"name":"workspace"}' }),
    })
    expect(response.status).toBe(200)
    expect(upstream).toHaveBeenCalledWith(
      `http://api.test:8000/v1/${path}`,
      expect.objectContaining({ method })
    )
  })

  test('deletes files with their ids in a JSON body', async () => {
    await call(DELETE, 'collections/workspace/documents', {
      method: 'DELETE',
      headers: { 'content-type': 'application/json' },
      body: '{"file_ids":["f1"]}',
    })
    const init = upstream.mock.calls[0][1] as RequestInit
    expect(init.method).toBe('DELETE')
    expect(init.body).toBe('{"file_ids":["f1"]}')
  })

  test('streams an upload with its multipart boundary', async () => {
    const form = new FormData()
    form.append('files', new File(['a,b\n1,2\n'], 'orders.csv', { type: 'text/csv' }))

    const response = await upload(form)

    expect(response.status).toBe(200)
    const init = upstream.mock.calls[0][1] as RequestInit & { duplex?: string }
    expect((init.headers as Headers).get('content-type')).toMatch(
      /^multipart\/form-data; boundary=/
    )
    expect(init.duplex).toBe('half')
    expect(init.body).toBeInstanceOf(ReadableStream)
    expect(await new Response(init.body).text()).toContain('filename="orders.csv"')
  })

  test('refuses an upload that is not a multipart form', async () => {
    const notMultipart = await call(POST, 'collections/workspace/documents', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: '{}',
    })
    expect(notMultipart.status).toBe(415)
    expect(upstream).not.toHaveBeenCalled()
  })

  test('cuts off a streamed upload once it passes the limit', async () => {
    vi.stubEnv('FILE_UPLOAD_MAX_REQUEST_MB', '0.001')
    upstream.mockImplementation(async (_url: string, init: RequestInit) => {
      // As Node's fetch does when its body stream fails
      try {
        await new Response(init.body).arrayBuffer()
      } catch (cause) {
        throw new TypeError('fetch failed', { cause })
      }
      return Response.json({ ok: true })
    })
    const form = new FormData()
    form.append('files', new File(['x'.repeat(4096)], 'big.txt'))

    expect((await upload(form)).status).toBe(413)
  })
  test.each([
    ['GET', 'collections'],
    // The ingest service creates the workspace collection itself
    ['POST', 'collections'],
    ['DELETE', 'collections/workspace'],
    ['GET', 'collections/other/documents'],
    ['POST', 'collections/other/documents'],
  ])('refuses %s /v1/%s', async (method, path) => {
    const handler = method === 'GET' ? GET : method === 'POST' ? POST : DELETE
    expect((await call(handler, path, { method })).status).toBe(404)
  })

  test.each([
    [{ 'sec-fetch-site': 'cross-site' }],
    [{ 'sec-fetch-site': 'same-site' }],
    [{ 'sec-fetch-site': 'none', origin: 'http://ui.test', host: 'ui.test' }],
    [{ origin: 'https://evil.example', host: 'ui.test' }],
    [{ origin: 'null', host: 'ui.test' }],
    [{ origin: 'http://ui.test:3300', host: 'ui.test:8080' }],
    [{ origin: 'http://ui.test', host: 'ui.test', 'x-forwarded-host': 'evil.example' }],
  ])('refuses a write another site sends (%j)', async (headers) => {
    for (const [handler, method, path] of [
      [POST, 'POST', 'collections/workspace/documents'],
      [DELETE, 'DELETE', 'collections/workspace/documents'],
      [POST, 'POST', 'jobs/async/submit'],
    ] as const) {
      const response = await call(handler, path, { method, headers, body: '{}' })
      expect(response.status).toBe(403)
    }
    expect(upstream).not.toHaveBeenCalled()
  })

  test.each([
    // Behind a reverse proxy or a shared link that forwards another Host: the browser's word decides
    [{ 'sec-fetch-site': 'same-origin', origin: 'https://share.example', host: 'ui:3000' }],
    // Without Sec-Fetch-Site: the Origin against the host the browser addressed, port included
    [{ origin: 'http://ui.test:3300', host: 'ui.test:3300' }],
    [{ origin: 'https://share.example', host: 'ui:3000', 'x-forwarded-host': 'share.example' }],
    // Not a browser (curl, the live test's API client): no Origin to check
    [{}],
  ])('allows a write from this site (%j)', async (headers) => {
    const response = await call(POST, 'jobs/async/submit', { method: 'POST', headers, body: '{}' })
    expect(response.status).toBe(200)
  })

  test('allows a same-origin write, and reads from anywhere', async () => {
    const same = { 'sec-fetch-site': 'same-origin', origin: 'http://ui.test', host: 'ui.test' }
    const form = new FormData()
    form.append('files', new File(['a'], 'a.txt'))
    const response = await POST(
      withHeaders(
        new Request('http://ui.test/api/v1/collections/workspace/documents', {
          method: 'POST',
          body: form,
        }),
        same
      ),
      { params: Promise.resolve({ path: UPLOAD_PATH }) }
    )
    expect(response.status).toBe(200)
    const read = await call(GET, 'collections/workspace/documents', {
      headers: { 'sec-fetch-site': 'cross-site' },
    })
    expect(read.status).toBe(200)
  })
})
