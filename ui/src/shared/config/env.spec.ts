// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { readApiUrl, readAppConfig, readRecordingsDir, readUiMode } from './env'

describe('runtime configuration', () => {
  test('defaults to live mode without Phoenix and the compose API address', () => {
    expect(readAppConfig({})).toEqual({
      mode: 'live',
      phoenixUrl: null,
      speechInput: { enabled: false, maxSeconds: 60 },
    })
    expect(readApiUrl({})).toBe('http://api:8000')
  })

  test('reads the mode and a Phoenix URL without a trailing slash', () => {
    expect(readAppConfig({ UI_MODE: 'replay', PHOENIX_URL: 'http://127.0.0.1:6006/' })).toEqual({
      mode: 'replay',
      phoenixUrl: 'http://127.0.0.1:6006',
      speechInput: { enabled: false, maxSeconds: 60 },
    })
  })

  test('shows voice input only when enabled in live mode, with a bounded recording length', () => {
    const enabled = { SPEECH_INPUT_ENABLED: 'true', SPEECH_INPUT_MAX_SECONDS: '120' }
    expect(readAppConfig(enabled).speechInput).toEqual({ enabled: true, maxSeconds: 90 })
    expect(readAppConfig({ ...enabled, UI_MODE: 'replay' }).speechInput.enabled).toBe(false)
    expect(readAppConfig({ SPEECH_INPUT_MAX_SECONDS: 'soon' }).speechInput.maxSeconds).toBe(60)
  })

  test('rejects invalid values instead of guessing', () => {
    expect(() => readUiMode({ UI_MODE: 'demo' })).toThrow('UI_MODE must be "live" or "replay"')
    expect(() => readAppConfig({ PHOENIX_URL: 'javascript:alert(1)' })).toThrow(
      'PHOENIX_URL must be an http(s) URL'
    )
    expect(() => readApiUrl({ API_URL: 'not a url' })).toThrow()
    expect(() => readRecordingsDir({ DATA_PACK: '../etc' })).toThrow('DATA_PACK must match')
  })

  test('resolves the active pack recordings under the packs directory', () => {
    expect(readRecordingsDir({})).toBe('/packs/synthetic-market/recordings')
    expect(readRecordingsDir({ PACKS_DIR: '/data/packs', DATA_PACK: 'other-pack' })).toBe(
      '/data/packs/other-pack/recordings'
    )
  })
})
