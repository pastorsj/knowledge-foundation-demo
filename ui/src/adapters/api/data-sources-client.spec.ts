// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, test, vi } from 'vitest'
import {
  CATALOG_BUILDING_MESSAGE,
  DataSourcesError,
  fetchDataSources,
  fetchRecordedDataSources,
} from './data-sources-client'

const SOURCES = [{ id: 'retail.policies', name: 'Store policies', pack_id: 'retail' }]

describe('fetchDataSources', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test.each([
    ['a wrapped list', { data_sources: SOURCES }],
    ['a bare list', SOURCES],
  ])('accepts %s', async (_shape, body) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body)))

    await expect(fetchDataSources('retail')).resolves.toEqual(SOURCES)
    expect(fetch).toHaveBeenCalledWith('/api/v1/data_sources?pack=retail', { signal: undefined })
  })

  test('asks for every pack’s sources without a pack', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(SOURCES)))

    await fetchDataSources()
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

    await expect(fetchDataSources('retail')).rejects.toThrow('The API is unavailable')
  })

  test('reads the API’s own error (FastAPI’s detail), which is not worth retrying by itself', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(Response.json({ detail: 'Unknown pack nope' }, { status: 404 }))
    )

    const error = await fetchDataSources('nope').catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(DataSourcesError)
    expect(error).toMatchObject({ message: 'Unknown pack nope', status: 404, retryable: false })
  })

  test('says a 503 is the catalog being built, and that it retries', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(Response.json({ detail: 'catalog unavailable' }, { status: 503 }))
    )

    const error = await fetchDataSources('retail').catch((caught: unknown) => caught)
    expect(error).toMatchObject({ message: CATALOG_BUILDING_MESSAGE, status: 503, retryable: true })
  })
})

describe('fetchRecordedDataSources', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  test('reads the sources of a pack’s replay bundle (sources.json), dropping malformed entries', async () => {
    const sources = [
      { id: 'retail.sales', name: 'Sales', kind: 'structured', database_name: 'retail_sales' },
      { id: 'retail.policies', name: 'Policies', kind: 'documents', database_name: null },
      { name: 'no id' },
    ]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(sources)))

    await expect(fetchRecordedDataSources('retail')).resolves.toEqual(sources.slice(0, 2))
    expect(fetch).toHaveBeenCalledWith('/api/recordings/retail/sources.json', {
      signal: undefined,
    })
  })

  test('fails when the bundle has no sources.json', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 404 })))

    await expect(fetchRecordedDataSources('retail')).rejects.toThrow('404')
  })
})
