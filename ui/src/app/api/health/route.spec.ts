// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, test, vi } from 'vitest'
import { GET } from './route'

describe('/api/health', () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  test('reports the UI as healthy with its mode, without calling the API', async () => {
    vi.stubEnv('UI_MODE', 'replay')
    const fetchSpy = vi.spyOn(globalThis, 'fetch')

    const response = GET()

    expect(await response.json()).toEqual({ status: 'ok', mode: 'replay' })
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
