// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test, vi } from 'vitest'
import { BrowserSpeechRecorder } from './browser-recorder'

describe('BrowserSpeechRecorder', () => {
  test('captures a frame, returns 16 kHz WAV, and releases every browser resource', async () => {
    const stopTrack = vi.fn()
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream
    const source = { connect: vi.fn(), disconnect: vi.fn() }
    const processor = {
      connect: vi.fn(),
      disconnect: vi.fn(),
      onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null,
    }
    const mute = { connect: vi.fn(), disconnect: vi.fn(), gain: { value: 1 } }
    const close = vi.fn().mockResolvedValue(undefined)
    const context = {
      state: 'running',
      sampleRate: 48_000,
      destination: {},
      createMediaStreamSource: vi.fn(() => source),
      createScriptProcessor: vi.fn(() => processor),
      createGain: vi.fn(() => mute),
      close,
    } as unknown as AudioContext
    const recorder = new BrowserSpeechRecorder({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      createAudioContext: () => context,
    })

    await recorder.start()
    processor.onaudioprocess?.({
      inputBuffer: {
        getChannelData: () => new Float32Array(480).fill(0.25),
      },
    } as unknown as AudioProcessingEvent)
    const wav = await recorder.stop()
    const view = new DataView(await wav.arrayBuffer())

    expect(view.getUint32(24, true)).toBe(16_000)
    expect(view.getUint32(40, true)).toBe(320)
    expect(stopTrack).toHaveBeenCalledOnce()
    expect(source.disconnect).toHaveBeenCalledOnce()
    expect(processor.disconnect).toHaveBeenCalledOnce()
    expect(mute.disconnect).toHaveBeenCalledOnce()
    expect(close).toHaveBeenCalledOnce()
    expect(processor.onaudioprocess).toBeNull()
  })

  test('caps captured samples at the configured server duration', async () => {
    const stream = { getTracks: () => [{ stop: vi.fn() }] } as unknown as MediaStream
    const source = { connect: vi.fn(), disconnect: vi.fn() }
    const processor = {
      connect: vi.fn(),
      disconnect: vi.fn(),
      onaudioprocess: null as ((event: AudioProcessingEvent) => void) | null,
    }
    const mute = { connect: vi.fn(), disconnect: vi.fn(), gain: { value: 1 } }
    const context = {
      state: 'running',
      sampleRate: 48_000,
      destination: {},
      createMediaStreamSource: vi.fn(() => source),
      createScriptProcessor: vi.fn(() => processor),
      createGain: vi.fn(() => mute),
      close: vi.fn().mockResolvedValue(undefined),
    } as unknown as AudioContext
    const recorder = new BrowserSpeechRecorder(
      {
        mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
        createAudioContext: () => context,
      },
      1
    )

    await recorder.start()
    for (let index = 0; index < 2; index += 1) {
      processor.onaudioprocess?.({
        inputBuffer: { getChannelData: () => new Float32Array(30_000).fill(0.25) },
      } as unknown as AudioProcessingEvent)
    }
    const view = new DataView(await (await recorder.stop()).arrayBuffer())

    expect(view.getUint32(40, true)).toBe(16_000 * 2)
  })

  test('stops a granted media track if audio setup fails', async () => {
    const stopTrack = vi.fn()
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream
    const recorder = new BrowserSpeechRecorder({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      createAudioContext: () => {
        throw new Error('audio context failed')
      },
    })

    await expect(recorder.start()).rejects.toThrow('audio context failed')
    expect(stopTrack).toHaveBeenCalledOnce()
  })

  test('closes a partially initialized audio context when node setup fails', async () => {
    const stopTrack = vi.fn()
    const close = vi.fn().mockResolvedValue(undefined)
    const stream = { getTracks: () => [{ stop: stopTrack }] } as unknown as MediaStream
    const context = {
      state: 'running',
      sampleRate: 48_000,
      createMediaStreamSource: vi.fn(() => {
        throw new Error('source setup failed')
      }),
      close,
    } as unknown as AudioContext
    const recorder = new BrowserSpeechRecorder({
      mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) },
      createAudioContext: () => context,
    })

    await expect(recorder.start()).rejects.toThrow('source setup failed')
    expect(stopTrack).toHaveBeenCalledOnce()
    expect(close).toHaveBeenCalledOnce()
  })
})
