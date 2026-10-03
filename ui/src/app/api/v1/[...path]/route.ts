// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * API Proxy Route
 *
 * Forwards `/api/v1/<path>` to `$API_URL/v1/<path>` so the browser only ever
 * talks to this origin and the API stays on the internal network.
 *
 * Only the public surface of the demo API is reachable (see ROUTES); anything
 * else, including the sandbox-only `/internal/**` routes, is a 404. Responses,
 * including Server-Sent Event streams, are passed through unbuffered. A voice
 * recording (`speech/transcriptions`) is forwarded as bytes, up to 3 MiB.
 *
 * Your data's documents routes (AI-Q's contract, which the API forwards to the
 * ingest service) are allowed too. An upload's multipart body is streamed, not
 * buffered, with its content type (and so its boundary), and is cut off with a
 * 413 past FILE_UPLOAD_MAX_REQUEST_MB.
 */

import { readApiUrl, readMaxUploadRequestBytes, readUiMode } from '@/shared/config/env'

type Method = 'GET' | 'POST' | 'DELETE'

/** The API routes the UI may call, matched against the path after `/v1/`. */
const ROUTES: ReadonlyArray<readonly [Method, RegExp]> = [
  ['GET', /^pack$/],
  ['GET', /^packs$/],
  ['GET', /^collections(\/[^/]+)?$/],
  ['POST', /^collections$/],
  ['DELETE', /^collections\/[^/]+$/],
  ['GET', /^collections\/[^/]+\/documents$/],
  ['POST', /^collections\/[^/]+\/documents$/],
  ['DELETE', /^collections\/[^/]+\/documents$/],
  ['GET', /^documents\/[^/]+\/status$/],
  ['GET', /^data_sources(\/.+)?$/],
  ['POST', /^data_sources\/[^/]+\/query$/],
  ['POST', /^jobs\/async\/submit$/],
  ['GET', /^jobs\/async\/job\/[^/]+(\/.+)?$/],
  ['POST', /^jobs\/async\/job\/[^/]+\/cancel$/],
  ['POST', /^speech\/transcriptions$/],
]

/** A voice recording: 16 kHz mono PCM16 WAV, at most 90 s (about 2.9 MB). */
const SPEECH_PATH = 'speech/transcriptions'
const MAX_SPEECH_BYTES = 3 * 1024 * 1024

/** One URL path segment: no traversal, no encoded separators. */
const SEGMENT = /^[A-Za-z0-9_][A-Za-z0-9._:-]*$/

/** Request headers forwarded to the API. */
const FORWARDED_REQUEST_HEADERS = ['accept', 'content-type', 'conversation-id', 'last-event-id']

/** Response headers passed back to the browser. */
const FORWARDED_RESPONSE_HEADERS = ['content-type', 'cache-control', 'content-disposition']

const errorResponse = (status: number, code: string, message: string): Response =>
  Response.json({ error: { code, message } }, { status })

/** The API URL for an allowed request, or null. */
const resolveTarget = (method: Method, segments: string[], search: string): string | null => {
  if (!segments.every((segment) => SEGMENT.test(segment))) return null
  const path = segments.join('/')
  if (!ROUTES.some(([allowed, pattern]) => allowed === method && pattern.test(path))) return null
  return `${readApiUrl()}/v1/${path}${search}`
}

/** An upload: a multipart body of files for a collection. */
const UPLOAD = /^collections\/[^/]+\/documents$/

const tooLarge = (): Response =>
  errorResponse(413, 'UPLOAD_TOO_LARGE', 'The upload is larger than this deployment accepts')

/** The upload's body as it arrives, failing once it passes `limit` bytes (and saying so in `cut`). */
const boundedStream = (
  body: ReadableStream<Uint8Array>,
  limit: number,
  cut: { exceeded: boolean }
): ReadableStream => {
  let received = 0
  return body.pipeThrough(
    new TransformStream<Uint8Array, Uint8Array>({
      transform(chunk, controller) {
        received += chunk.byteLength
        if (received <= limit) return controller.enqueue(chunk)
        cut.exceeded = true
        controller.error(new RangeError('The upload is too large'))
      },
    })
  )
}

/** An upload's body, streamed and bounded; a declared length past the limit is refused at once. */
const readUpload = (request: Request, cut: { exceeded: boolean }): BodyInit | Response => {
  const limit = readMaxUploadRequestBytes()
  if (!request.headers.get('content-type')?.toLowerCase().startsWith('multipart/form-data')) {
    return errorResponse(415, 'UNSUPPORTED_UPLOAD', 'An upload is a multipart form of files')
  }
  if (Number(request.headers.get('content-length') ?? 0) > limit) return tooLarge()
  return request.body ? boundedStream(request.body, limit, cut) : ''
}

/** The request body to forward: text, or a bounded voice recording as bytes. */
const readBody = async (request: Request, speech: boolean): Promise<BodyInit | Response> => {
  if (!speech) return request.text()
  if (request.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !== 'audio/wav') {
    return errorResponse(415, 'UNSUPPORTED_AUDIO', 'Voice input requires a WAV recording')
  }
  if (Number(request.headers.get('content-length') ?? 0) > MAX_SPEECH_BYTES) {
    return errorResponse(413, 'AUDIO_TOO_LARGE', 'The recording is too large')
  }
  const body = await request.arrayBuffer()
  return body.byteLength > MAX_SPEECH_BYTES
    ? errorResponse(413, 'AUDIO_TOO_LARGE', 'The recording is too large')
    : body
}

const proxy = async (
  request: Request,
  method: Method,
  params: Promise<{ path: string[] }>
): Promise<Response> => {
  if (readUiMode() === 'replay') {
    return errorResponse(404, 'REPLAY_MODE', 'The API is not available in replay mode')
  }

  const segments = (await params).path
  const target = resolveTarget(method, segments, new URL(request.url).search)
  if (!target) return errorResponse(404, 'NOT_FOUND', 'Not found')
  const path = segments.join('/')
  const upload = method === 'POST' && UPLOAD.test(path)
  const cut = { exceeded: false }
  const body = upload
    ? readUpload(request, cut)
    : method === 'GET'
      ? undefined
      : await readBody(request, path === SPEECH_PATH)
  if (body instanceof Response) return body

  const headers = new Headers()
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name)
    if (value) headers.set(name, value)
  }

  let upstream: Response
  try {
    upstream = await fetch(target, {
      method,
      headers,
      body,
      cache: 'no-store',
      // Closing the browser stream closes the upstream SSE connection.
      signal: request.signal,
      // A streamed upload is sent as it arrives (Node's fetch requires saying so)
      ...(upload ? { duplex: 'half' } : {}),
    } as RequestInit)
  } catch {
    if (cut.exceeded) return tooLarge()
    return errorResponse(502, 'PROXY_ERROR', 'The API is unavailable')
  }

  const responseHeaders = new Headers()
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name)
    if (value) responseHeaders.set(name, value)
  }
  if (upstream.headers.get('content-type')?.startsWith('text/event-stream')) {
    responseHeaders.set('cache-control', 'no-cache, no-transform')
    responseHeaders.set('x-accel-buffering', 'no')
  }

  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders })
}

interface RouteContext {
  params: Promise<{ path: string[] }>
}

export const GET = (request: Request, { params }: RouteContext): Promise<Response> =>
  proxy(request, 'GET', params)

export const POST = (request: Request, { params }: RouteContext): Promise<Response> =>
  proxy(request, 'POST', params)

export const DELETE = (request: Request, { params }: RouteContext): Promise<Response> =>
  proxy(request, 'DELETE', params)
