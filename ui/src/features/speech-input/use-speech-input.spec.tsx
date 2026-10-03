// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, test, vi } from 'vitest'
import type { SpeechRecorder } from './browser-recorder'
import { useSpeechInput } from './use-speech-input'

const createRecorder = (overrides: Partial<SpeechRecorder> = {}): SpeechRecorder => ({
  start: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue(new Blob([new Uint8Array(48)], { type: 'audio/wav' })),
  cancel: vi.fn().mockResolvedValue(undefined),
  ...overrides,
})

describe('useSpeechInput', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  test('records, transcribes, and returns to idle without submitting anything itself', async () => {
    const recorder = createRecorder()
    const onTranscript = vi.fn()
    const transcribe = vi.fn().mockResolvedValue({ text: 'Show delayed deployments' })
    const { result } = renderHook(() =>
      useSpeechInput({
        enabled: true,
        maxSeconds: 60,
        onTranscript,
        createRecorder: () => recorder,
        transcribe,
      })
    )

    await act(() => result.current.start())
    expect(result.current.state).toBe('recording')
    await act(() => result.current.stop())

    expect(transcribe).toHaveBeenCalledOnce()
    expect(onTranscript).toHaveBeenCalledWith('Show delayed deployments')
    expect(result.current.state).toBe('idle')
    expect(result.current.error).toBeNull()
  })

  test('stops automatically at the configured duration', async () => {
    vi.useFakeTimers()
    const recorder = createRecorder()
    const transcribe = vi.fn().mockResolvedValue({ text: 'Finished automatically' })
    const onTranscript = vi.fn()
    const { result } = renderHook(() =>
      useSpeechInput({
        enabled: true,
        maxSeconds: 5,
        onTranscript,
        createRecorder: () => recorder,
        transcribe,
      })
    )

    await act(() => result.current.start())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000)
    })

    expect(recorder.stop).toHaveBeenCalledOnce()
    expect(onTranscript).toHaveBeenCalledWith('Finished automatically')
  })

  test('maps microphone permission failures to a display-safe error', async () => {
    const recorder = createRecorder({
      start: vi
        .fn()
        .mockRejectedValue(new DOMException('private device details', 'NotAllowedError')),
    })
    const { result } = renderHook(() =>
      useSpeechInput({
        enabled: true,
        maxSeconds: 60,
        onTranscript: vi.fn(),
        createRecorder: () => recorder,
      })
    )

    await act(() => result.current.start())

    expect(result.current.state).toBe('error')
    expect(result.current.error).toBe(
      'Microphone permission was denied. Allow microphone access and try again.'
    )
    expect(result.current.error).not.toContain('private device details')
  })

  test('aborts transcription and releases recording resources on unmount', async () => {
    const recorder = createRecorder()
    let receivedSignal: AbortSignal | undefined
    const transcribe = vi.fn((_blob: Blob, options?: { signal?: AbortSignal }) => {
      receivedSignal = options?.signal
      return new Promise<{ text: string }>(() => undefined)
    })
    const { result, unmount } = renderHook(() =>
      useSpeechInput({
        enabled: true,
        maxSeconds: 60,
        onTranscript: vi.fn(),
        createRecorder: () => recorder,
        transcribe,
      })
    )

    await act(() => result.current.start())
    act(() => {
      void result.current.stop()
    })
    await waitFor(() => expect(transcribe).toHaveBeenCalledOnce())
    unmount()

    expect(receivedSignal?.aborted).toBe(true)
  })

  test('cancels a pending permission request when voice input becomes unavailable', async () => {
    let grantPermission: (() => void) | undefined
    const recorder = createRecorder({
      start: vi.fn(
        () =>
          new Promise<void>((resolve) => {
            grantPermission = resolve
          })
      ),
    })
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) =>
        useSpeechInput({
          enabled,
          maxSeconds: 60,
          onTranscript: vi.fn(),
          createRecorder: () => recorder,
        }),
      { initialProps: { enabled: true } }
    )

    act(() => {
      void result.current.start()
    })
    await waitFor(() => expect(result.current.state).toBe('requesting-permission'))
    rerender({ enabled: false })
    await act(async () => grantPermission?.())

    await waitFor(() => expect(recorder.cancel).toHaveBeenCalled())
    expect(result.current.state).toBe('idle')
  })
})
