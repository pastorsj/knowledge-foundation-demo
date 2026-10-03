// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, expectTypeOf, test, vi } from 'vitest'
import type { z } from 'zod'
import type { PackView } from '@/generated/pack'
import { fetchPack, fetchPacks, type PackSchema } from './pack-client'

/** What the API sends, typed by the generated contract */
const PACK: PackView = {
  id: 'retail',
  kind: 'industry',
  title: 'Retail',
  description: null,
  icon: 'Store',
  status: 'ready',
  version: '1.0.0',
  as_of: '2026-09-30',
  disclaimer: 'Synthetic data for a software demonstration.',
  questions: [
    {
      id: 'top-customers',
      label: 'Top Customers',
      tag: 'ANALYTICS',
      description: null,
      question: 'Which customers spent the most?',
      sources: ['retail.sales'],
      tools: ['duckdb', 'quantum'],
      featured: true,
    },
    {
      id: 'outlook',
      label: 'Outlook',
      tag: 'PREDICTION',
      description: null,
      question: 'What comes next?',
      sources: ['retail.sales'],
      tools: ['kumo'],
      featured: false,
    },
  ],
  examples: ['outlook', 'top-customers'],
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
    expect(pack).toMatchObject({ kind: 'industry', icon: 'Store', status: 'ready' })
    expect(pack?.questions[0]).toMatchObject({
      id: 'top-customers',
      tools: ['duckdb'], // only the pills the UI knows
      featured: true,
    })
  })

  test('reads a chosen pack by id', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(PACK)))

    await fetchPack('financial-services')
    expect(fetch).toHaveBeenCalledWith(
      'http://api.test:8000/v1/pack?id=financial-services',
      expect.anything()
    )
  })

  test('lists the packs a user can pick, and none when the API is down', async () => {
    const packs = {
      packs: [
        {
          id: 'retail',
          kind: 'industry',
          title: 'Retail',
          description: null,
          icon: 'Store',
          status: 'ready',
        },
        { id: 'workspace', kind: 'workspace', title: 'Your data', icon: 'Upload', status: 'empty' },
      ],
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(packs)))
    expect(await fetchPacks()).toEqual([packs.packs[0], { ...packs.packs[1], description: null }])
    expect(fetch).toHaveBeenCalledWith('http://api.test:8000/v1/packs', expect.anything())

    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('fetch failed')))
    expect(await fetchPacks()).toEqual([])
  })

  test("reads the example picker's questions in their order", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(PACK)))

    expect((await fetchPack())?.examples).toEqual(['outlook', 'top-customers'])
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
