// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import type { AnalyticsResultReceipt } from './contract'
import { receiptOutputCount, summarizeReceipt } from './receipt-summary'
import { receiptOf } from './test-utils/fixtures'

const analytics = (
  operationId: NonNullable<AnalyticsResultReceipt['content']>['operationId'],
  payload: Record<string, unknown>
): AnalyticsResultReceipt => {
  const receipt = receiptOf('analytics_result')
  return { ...receipt, content: { ...receipt.content!, operationId, payload } }
}

describe('summarizeReceipt', () => {
  it('describes a retrieval: sources, index, models, timings, the query and its passages', () => {
    const summary = summarizeReceipt(receiptOf('retrieval_evidence'))
    const content = receiptOf('retrieval_evidence').content!

    expect(summary.title).toBe('Unstructured retrieval result')
    expect(summary.summary).toMatch(/^\d+ passages? displayed from \d+ candidates?\.$/)
    expect(summary.details).toEqual(
      expect.arrayContaining([
        'Sources: market_news',
        `Collection: ${content.collection}`,
        `Embedding model: ${content.models.embed}`,
        `Total retrieval: ${content.timings.totalMs.toFixed(1)} ms`,
      ])
    )
    expect(summary.statement).toMatchObject({ label: 'Search query', language: 'text' })
    expect(summary.output?.kind).toBe('passages')
    if (summary.output?.kind !== 'passages') return
    expect(summary.output.passages[0]).toMatchObject({ source: content.hits[0].title })
    // A negative rerank logit is left out, as the original did
    expect(summary.output.passages[0].metadata.some((m) => m.startsWith('Rerank score'))).toBe(
      content.hits[0].score >= 0
    )
    expect(summary.details).toContain(`Vector index: ${content.index.type} (CPU)`)
  })

  it('describes an Auto Ontology answer: its SQL and its rows', () => {
    const content = receiptOf('structured_query').content!
    const summary = summarizeReceipt(receiptOf('structured_query'))

    expect(summary.title).toBe('Structured result')
    expect(summary.details).toEqual(['Database: market_analysis'])
    expect(summary.statement).toMatchObject({ label: 'Generated SQL', language: 'sql' })
    expect(summary.output).toMatchObject({
      kind: 'table',
      label: 'Query result',
      displayedCount: content.rows.length,
    })
  })

  it('describes a Kumo prediction in Kumo’s columns, under the database it read', () => {
    const summary = summarizeReceipt(receiptOf('structured_prediction'), {
      databaseName: 'market_analysis',
    })

    expect(summary.summary).toMatch(/^\d+ predictions returned\.$/)
    expect(summary.details).toEqual(['Database: market_analysis'])
    expect(summary.statement).toMatchObject({ label: 'Generated PQL', language: 'pql' })
    expect(summary.output).toMatchObject({
      label: 'Prediction result',
      columns: ['ANCHOR_TIMESTAMP', 'ENTITY', 'FALSE_PROB', 'PREDICTION', 'TRUE_PROB'],
    })
    if (summary.output?.kind !== 'table') return
    // FALSE_PROB is TRUE_PROB's complement; PREDICTION is the likelier class
    expect(summary.output.rows[0]).toEqual([
      '2026-08-24T21:00:00Z',
      'asset-delta',
      String(1 - 0.8469578623771667),
      'true',
      '0.8469578623771667',
    ])
    expect(summary.output.rows.at(-1)?.[3]).toBe('false')
  })

  it('lists a Kumo prediction’s entities by TRUE_PROB, the likeliest first, as the original did', () => {
    const receipt = receiptOf('structured_prediction')
    const content = receipt.content!
    const rows = [...content.rows].reverse() // as a recording may hold them, in ticker order say
    const summary = summarizeReceipt(
      { ...receipt, content: { ...content, rows } },
      { databaseName: 'market_analysis' }
    )
    if (summary.output?.kind !== 'table') throw new Error('no table')
    const shown = summary.output.rows.map((row) => Number(row[4]))
    expect(shown).toEqual([...shown].sort((a, b) => b - a))
    expect(summary.output.rows[0]?.[1]).toBe('asset-delta')
  })

  it('reports a failed call without its content', () => {
    const summary = summarizeReceipt(receiptOf('structured_prediction', 'failed'), {
      databaseName: 'market_analysis',
    })
    expect(summary).toMatchObject({
      title: 'Tool result',
      summary: 'The tool call ended with a failure.',
      details: ['Database: market_analysis'],
    })
    expect(summary.output).toBeUndefined()
    const failedQuery = { ...receiptOf('structured_query'), status: 'failed' as const }
    expect(summarizeReceipt(failedQuery, { databaseName: 'market_analysis' }).details).toEqual([])
  })

  it('describes each market operation by its own rows and facts', () => {
    const anomaly = summarizeReceipt(receiptOf('analytics_result'))
    expect(anomaly.summary).toBe('5 ranked anomaly observations returned.')
    expect(anomaly.details).toEqual(
      expect.arrayContaining([
        'Engine: cuml.accel 26.6.0 (GPU)',
        'Training observations: 1476',
        'Flagged observations: 31',
      ])
    )
    expect(anomaly.statement).toMatchObject({ label: 'Public parameters', language: 'json' })

    const scan = summarizeReceipt(
      analytics('market_scan', {
        assets_ranked: 12,
        observations: [{ rank: 1, asset_id: 'a', score: 0.1, values: { return: 0.1 } }],
      })
    )
    expect(scan.summary).toBe('1 row returned from 12 source rows.')
    expect(scan.output).toMatchObject({ truncated: true, sourceCount: 12 })

    const network = summarizeReceipt(
      analytics('analyze_market_relationships', {
        window_start: '2026-06-01',
        window_end: '2026-08-31',
        node_count: 12,
        edge_count: 40,
        central_assets: [{ rank: 1, asset_id: 'a', centrality: 0.2 }],
        strongest_edges: [],
      })
    )
    expect(network.summary).toBe('1 ranked asset returned.')
    expect(network.details).toContain('Window: 2026-06-01 to 2026-08-31')
  })

  it('counts rows or passages, and says when they are a part of more', () => {
    expect(
      receiptOutputCount({
        kind: 'table',
        label: 'Query result',
        columns: [],
        rows: [],
        displayedCount: 8,
        sourceCount: 8,
        truncated: false,
      })
    ).toBe('8 rows')
    expect(
      receiptOutputCount({
        kind: 'passages',
        label: 'Retrieved passages',
        passages: [],
        displayedCount: 1,
        sourceCount: 32,
        truncated: true,
      })
    ).toBe('1 passage of 32')
  })
})
