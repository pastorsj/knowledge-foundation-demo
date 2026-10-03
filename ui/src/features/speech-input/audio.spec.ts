// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { combineAudioChunks, encodePcm16Wav, resampleMonoAudio } from './audio'

describe('speech audio encoding', () => {
  test('downsamples common 48 kHz browser audio to 16 kHz mono', () => {
    const source = Float32Array.from({ length: 480 }, (_, index) => index / 480)

    const result = resampleMonoAudio(source, 48_000, 16_000)

    expect(result).toHaveLength(160)
    expect(result[0]).toBeCloseTo((source[0] + source[1] + source[2]) / 3)
  })

  test('combines capture frames without losing their order', () => {
    const combined = combineAudioChunks([
      new Float32Array([0.1, 0.2]),
      new Float32Array([0.3, 0.4]),
    ])

    expect(combined).toHaveLength(4)
    expect(combined[0]).toBeCloseTo(0.1)
    expect(combined[1]).toBeCloseTo(0.2)
    expect(combined[2]).toBeCloseTo(0.3)
    expect(combined[3]).toBeCloseTo(0.4)
  })

  test('writes a standards-compliant mono PCM16 WAV header and clamps samples', async () => {
    const wav = encodePcm16Wav(new Float32Array([-2, -0.5, 0, 0.5, 2]), 16_000)
    const view = new DataView(await wav.arrayBuffer())
    const text = (offset: number, length: number) =>
      String.fromCharCode(...new Uint8Array(view.buffer, offset, length))

    expect(wav.type).toBe('audio/wav')
    expect(wav.size).toBe(54)
    expect(text(0, 4)).toBe('RIFF')
    expect(text(8, 4)).toBe('WAVE')
    expect(view.getUint16(20, true)).toBe(1)
    expect(view.getUint16(22, true)).toBe(1)
    expect(view.getUint32(24, true)).toBe(16_000)
    expect(view.getUint16(34, true)).toBe(16)
    expect(text(36, 4)).toBe('data')
    expect(view.getInt16(44, true)).toBe(-32768)
    expect(view.getInt16(52, true)).toBe(32767)
  })
})
