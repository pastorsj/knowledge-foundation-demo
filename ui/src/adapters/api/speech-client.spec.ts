// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { SpeechTranscriptionError, transcribeSpeech } from './speech-client'

const fetchMock = vi.fn()

describe('transcribeSpeech', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  test('posts raw WAV audio to the same-origin speech endpoint', async () => {
    const audio = new Blob([new Uint8Array(48)], { type: 'audio/wav' })
    const abortController = new AbortController()
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ text: '  What is at risk?  ' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    )

    await expect(transcribeSpeech(audio, { signal: abortController.signal })).resolves.toEqual({
      text: 'What is at risk?',
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/speech/transcriptions',
      expect.objectContaining({
        method: 'POST',
        body: audio,
        signal: abortController.signal,
        headers: expect.objectContaining({ 'Content-Type': 'audio/wav' }),
      })
    )
  })

  test('rejects empty audio before making a network request', async () => {
    await expect(transcribeSpeech(new Blob([new Uint8Array(44)]))).rejects.toThrow(
      'No speech was captured'
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('maps provider failures without exposing an upstream response body', async () => {
    fetchMock.mockResolvedValue(new Response('secret provider response', { status: 503 }))

    const error = await transcribeSpeech(new Blob([new Uint8Array(48)])).catch(
      (caught: unknown) => caught
    )

    expect(error).toBeInstanceOf(SpeechTranscriptionError)
    expect((error as Error).message).toBe('Voice transcription is temporarily unavailable.')
    expect((error as Error).message).not.toContain('secret provider response')
  })

  test('rejects a successful response without recognized text', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ text: '' }), { status: 200 }))

    await expect(transcribeSpeech(new Blob([new Uint8Array(48)]))).rejects.toThrow(
      'No speech was recognized'
    )
  })
})
