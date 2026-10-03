// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import type { PackQuestion } from '@/adapters/api/pack-client'
import {
  getActiveDemoScenario,
  getAvailableDemoScenarios,
  MAX_EXAMPLES,
  toDemoScenarios,
} from './scenarios'

const SCENARIOS = toDemoScenarios([
  {
    id: 'market-leaders',
    label: 'Market Leaders',
    tools: ['cudf'],
    description: 'Scan the most liquid issuers.',
    question: 'Which issuers led?',
    sources: ['market_data'],
    featured: true,
  },
  {
    id: 'cyber-disclosure-rules',
    label: 'Cybersecurity Disclosures',
    tools: ['retrieval'],
    question: 'What does Item 1.05 require?',
    sources: ['sec_filings', 'market_regulations'],
    featured: false,
  },
])

describe('demo scenarios', () => {
  test('come from the pack questions, with the tools they are expected to use', () => {
    expect(SCENARIOS[0]).toEqual({
      id: 'market-leaders',
      label: 'Market Leaders',
      tools: ['cudf'],
      description: 'Scan the most liquid issuers.',
      question: 'Which issuers led?',
      sourceIds: ['market_data'],
    })
    // A question without a description describes itself
    expect(SCENARIOS[1].description).toBe('What does Item 1.05 require?')
  })

  test("are the pack's examples, in their order", () => {
    const questions: PackQuestion[] = ['a', 'b', 'c', 'd'].map((id) => ({
      id,
      label: id.toUpperCase(),
      tools: ['cudf'],
      question: `Question ${id}?`,
      sources: ['market_data'],
      featured: id === 'd',
    }))

    // Only the examples, in the pack's order; an id the API did not send is skipped
    expect(toDemoScenarios(questions, ['c', 'a', 'gone', 'd']).map((s) => s.id)).toEqual([
      'c',
      'a',
      'd',
    ])
    // An API without the list: the featured questions, then the others
    expect(toDemoScenarios(questions).map((s) => s.id)).toEqual(['d', 'a', 'b', 'c'])
    expect(toDemoScenarios(questions, null).map((s) => s.id)).toEqual(['d', 'a', 'b', 'c'])
  })

  test('are at most twelve', () => {
    const questions: PackQuestion[] = Array.from({ length: 20 }, (_, i) => ({
      id: `q${i}`,
      label: `Q${i}`,
      tools: ['cudf'],
      question: `Question ${i}?`,
      sources: ['market_data'],
      featured: i === 15,
    }))

    expect(MAX_EXAMPLES).toBe(12)
    expect(toDemoScenarios(questions).map((s) => s.id)).toEqual([
      'q15',
      ...Array.from({ length: 11 }, (_, i) => `q${i}`),
    ])
    expect(toDemoScenarios(questions, questions.map((q) => q.id).reverse())).toHaveLength(12)
  })

  test('are offered only when every data source they need is available', () => {
    expect(getAvailableDemoScenarios(SCENARIOS, ['market_data']).map((s) => s.id)).toEqual([
      'market-leaders',
    ])
    expect(
      getAvailableDemoScenarios(SCENARIOS, ['market_data', 'sec_filings', 'market_regulations'])
    ).toHaveLength(2)
  })

  test('the active one has the staged question and exactly its data sources', () => {
    expect(getActiveDemoScenario('Which issuers led?', ['market_data'], SCENARIOS)?.id).toBe(
      'market-leaders'
    )
    expect(
      getActiveDemoScenario('Which issuers led?', ['market_data', 'sec_filings'], SCENARIOS)
    ).toBeUndefined()
    expect(getActiveDemoScenario('Something else', ['market_data'], SCENARIOS)).toBeUndefined()
  })
})
