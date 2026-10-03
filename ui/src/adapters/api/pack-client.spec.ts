// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, expectTypeOf, test, vi } from 'vitest'
import type { z } from 'zod'
import type { PackView } from '@/generated/pack'
import { fetchPack, type PackSchema } from './pack-client'

/** What the API sends, typed by the generated contract */
const PACK: PackView = {
  id: 'market-analysis',
  version: '1.0.0',
  title: 'Synthetic Multi-Asset Market Analysis',
  description: null,
  as_of: '2026-08-31',
  disclaimer: 'Synthetic market data for a software demonstration. Not investment advice.',
  questions: [
    {
      id: 'market-leaders',
      label: 'Market Leaders',
      tag: 'ANALYTICS',
      description: null,
      question: 'Which assets had the strongest returns?',
      sources: ['market_analysis_structured'],
      tools: ['cudf', 'quantum'],
      featured: true,
    },
    {
      id: 'outlook',
      label: 'Outlook',
      tag: 'PREDICTION',
      description: null,
      question: 'What comes next?',
      sources: ['market_analysis_structured'],
      tools: ['kumo'],
      featured: false,
    },
  ],
  examples: ['outlook', 'market-leaders'],
  conversations: [],
}

describe('fetchPack', () => {
  beforeEach(() => {
    vi.stubEnv('API_URL', 'http://api.test:8000')
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  test('reads the pack view from the API', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(PACK)))

    const pack = await fetchPack()

    expect(fetch).toHaveBeenCalledWith('http://api.test:8000/v1/pack', expect.anything())
    expect(pack?.questions[0]).toMatchObject({
      id: 'market-leaders',
      tools: ['cudf'], // only the pills the UI knows
      featured: true,
    })
  })

  test("reads the example picker's questions in their order", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(PACK)))

    expect((await fetchPack())?.examples).toEqual(['outlook', 'market-leaders'])
  })

  test('accepts a pack from an API without examples', async () => {
    const { examples: _, ...older } = PACK
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(older)))

    const pack = await fetchPack()
    expect(pack?.questions).toHaveLength(2)
    expect(pack?.examples).toBeUndefined()
  })

  test('parses every pack view the contract allows', () => {
    expectTypeOf<PackView>().toExtend<z.input<typeof PackSchema>>()
  })

  test.each([
    ['the API is unreachable', () => Promise.reject(new TypeError('fetch failed'))],
    ['the API errors', () => Promise.resolve(new Response('nope', { status: 500 }))],
    ['the body has another shape', () => Promise.resolve(Response.json({ questions: 'none' }))],
  ])('returns null when %s', async (_case, response) => {
    vi.stubGlobal('fetch', vi.fn(response))

    await expect(fetchPack()).resolves.toBeNull()
  })
})
