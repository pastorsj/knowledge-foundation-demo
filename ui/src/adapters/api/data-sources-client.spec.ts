// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, test, vi } from 'vitest'
import { fetchDataSources, fetchRecordedDataSources } from './data-sources-client'

const SOURCES = [{ id: 'retail.policies', name: 'Market news' }]

describe('fetchDataSources', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test.each([
    ['a wrapped list', { data_sources: SOURCES }],
    ['a bare list', SOURCES],
  ])('accepts %s', async (_shape, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))

    await expect(fetchDataSources()).resolves.toEqual(SOURCES)
    expect(fetch).toHaveBeenCalledWith('/api/v1/data_sources', { signal: undefined })
  })

  test('throws the proxy error message', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          Response.json({ error: { message: 'The API is unavailable' } }, { status: 502 })
        )
    )

    await expect(fetchDataSources()).rejects.toThrow('The API is unavailable')
  })
})

describe('fetchRecordedDataSources', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('reads the sources of the replay bundle pack.json', async () => {
    const pack = {
      id: 'synthetic-market',
      sources: [
        { id: 'retail.sales', name: 'Market data', description: 'Prices', kind: 'structured' },
        { id: 'retail.policies', name: 'Market news', description: 'Filings', kind: 'documents' },
      ],
      structured: { source: 'retail.sales', database_name: 'synthetic_market' },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(pack)))

    await expect(fetchRecordedDataSources()).resolves.toEqual([
      {
        id: 'retail.sales',
        name: 'Market data',
        description: 'Prices',
        kind: 'structured',
        database_name: 'synthetic_market',
      },
      {
        id: 'retail.policies',
        name: 'Market news',
        description: 'Filings',
        kind: 'documents',
        database_name: null,
      },
    ])
    expect(fetch).toHaveBeenCalledWith('/api/recordings/pack.json', { signal: undefined })
  })

  test('fails when the bundle has no pack.json', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 404 })))

    await expect(fetchRecordedDataSources()).rejects.toThrow('404')
  })
})
