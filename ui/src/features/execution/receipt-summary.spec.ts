// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import type { RetrievalEvidenceReceipt, StructuredPredictionReceipt } from './contract'
import { receiptOutputCount, summarizeReceipt } from './receipt-summary'
import { fixtureReceipts, receiptOf } from './test-utils/fixtures'

/** The retrieval of the workspace's uploads, which ran without a rerank model */
const unreranked = fixtureReceipts.find(
  (receipt) => receipt.artifactKind === 'retrieval_evidence' && !receipt.content?.models.rerank
) as RetrievalEvidenceReceipt

const prediction = (
  rows: NonNullable<StructuredPredictionReceipt['content']>['rows']
): StructuredPredictionReceipt => {
  const receipt = receiptOf('structured_prediction')
  return { ...receipt, content: { ...receipt.content!, rows } }
}

describe('summarizeReceipt', () => {
  it('describes a retrieval: sources, index, models, timings, the query and its passages', () => {
    const summary = summarizeReceipt(receiptOf('retrieval_evidence'))
    const content = receiptOf('retrieval_evidence').content!

    expect(summary.title).toBe('Unstructured retrieval result')
    expect(summary.summary).toMatch(/^\d+ passages? displayed from \d+ candidates?\.$/)
    expect(summary.details).toEqual(
      expect.arrayContaining([
        'Sources: retail.policies',
        `Collection: ${content.collection}`,
        `Embedding model: ${content.models.embed}`,
        'Ranking: reranker',
        `Reranker model: ${content.models.rerank}`,
        `Reranking: ${content.timings.rerankMs.toFixed(1)} ms`,
        `Total retrieval: ${content.timings.totalMs.toFixed(1)} ms`,
      ])
    )
    expect(summary.statement).toMatchObject({ label: 'Search query', language: 'text' })
    expect(summary.output?.kind).toBe('passages')
    if (summary.output?.kind !== 'passages') return
    expect(summary.output.passages[0]).toMatchObject({ source: content.hits[0].title })
    expect(summary.output.passages[0].metadata).toEqual(
      expect.arrayContaining([
        `Rerank score: ${content.hits[0].score}`,
        'Parser: nemotron-parse-2.0',
        'Citation: return-policy.pdf, p. 1',
      ])
    )
    expect(summary.details).toContain(`Vector index: ${content.index.type} (CPU)`)
  })

  it('describes a retrieval without a rerank model in vector order, with no rerank figures', () => {
    const summary = summarizeReceipt(unreranked)

    expect(summary.details).toContain('Ranking: vector score (no rerank model)')
    expect(summary.details.some((detail) => detail.startsWith('Reranker model'))).toBe(false)
    expect(summary.details.some((detail) => detail.startsWith('Reranking'))).toBe(false)
    if (summary.output?.kind !== 'passages') throw new Error('no passages')
    expect(summary.output.passages[0].metadata.some((m) => m.startsWith('Rerank score'))).toBe(
      false
    )
    expect(summary.output.passages[0].metadata).toContain('Parser: pdf-text-layer')
  })

  it('describes a DuckDB table query: its database, SQL and rows', () => {
    const content = receiptOf('structured_query').content!
    const summary = summarizeReceipt(receiptOf('structured_query'))

    expect(summary.title).toBe('Structured result')
    expect(summary.details).toEqual(['Database: retail_sales'])
    expect(summary.statement).toMatchObject({ label: 'Generated SQL', language: 'sql' })
    expect(summary.statement?.value).toMatch(/^SELECT c\.tier/)
    expect(summary.output).toMatchObject({
      kind: 'table',
      label: 'Query result',
      columns: ['tier', 'orders', 'net_revenue'],
      displayedCount: content.rows.length,
    })
  })

  it('describes a binary Kumo prediction in Kumo’s columns, with its source and task', () => {
    const summary = summarizeReceipt(receiptOf('structured_prediction'))

    expect(summary.summary).toBe('3 predictions returned.')
    expect(summary.details).toEqual([
      'Source: retail.sales',
      'Template: churn_90d',
      'Task: binary classification',
      'Entities: customers',
      'Anchor time: 2026-09-30T00:00:00Z',
      'Horizon: 90 days',
      'Model: kumo-relational',
    ])
    expect(summary.statement).toMatchObject({ label: 'Generated PQL', language: 'pql' })
    expect(summary.output).toMatchObject({
      label: 'Prediction result',
      columns: ['ANCHOR_TIMESTAMP', 'ENTITY', 'FALSE_PROB', 'PREDICTION', 'TRUE_PROB'],
    })
    if (summary.output?.kind !== 'table') return
    // FALSE_PROB is TRUE_PROB's complement; PREDICTION is the likelier class
    expect(summary.output.rows[0]).toEqual([
      '2026-09-30T00:00:00Z',
      'C2',
      String(1 - 0.8132),
      'true',
      '0.8132',
    ])
    expect(summary.output.rows.at(-1)?.[3]).toBe('false')
  })

  it('lists entities by score, the likeliest first, whatever order the receipt holds them in', () => {
    const rows = [...receiptOf('structured_prediction').content!.rows].reverse()
    const summary = summarizeReceipt(prediction(rows))
    if (summary.output?.kind !== 'table') throw new Error('no table')
    const shown = summary.output.rows.map((row) => Number(row[4]))
    expect(shown).toEqual([...shown].sort((a, b) => b - a))
    expect(summary.output.rows[0]?.[1]).toBe('C2')
  })

  it('shows a regression’s values and a multiclass task’s classes in their own columns', () => {
    const regression = summarizeReceipt(
      prediction([
        { entityId: 'C1', probability: null, value: 120.5, label: null },
        { entityId: 'C2', probability: null, value: 310, label: null },
      ])
    )
    expect(regression.output).toMatchObject({
      columns: ['ANCHOR_TIMESTAMP', 'ENTITY', 'PREDICTION'],
      rows: [
        ['2026-09-30T00:00:00Z', 'C2', '310'],
        ['2026-09-30T00:00:00Z', 'C1', '120.5'],
      ],
    })

    const multiclass = summarizeReceipt(
      prediction([{ entityId: 'C3', probability: 0.64, value: null, label: 'gold' }])
    )
    expect(multiclass.output).toMatchObject({
      columns: ['ANCHOR_TIMESTAMP', 'ENTITY', 'CLASS', 'SCORE'],
      rows: [['2026-09-30T00:00:00Z', 'C3', 'gold', '0.64']],
    })
  })

  it('reports a failed call without its content, with the reason a prediction could not run', () => {
    const summary = summarizeReceipt(receiptOf('structured_prediction', 'failed'))
    expect(summary).toMatchObject({
      title: 'Tool result',
      summary: 'The tool call ended with a failure.',
      details: ['Source: retail.sales'],
      notices: ['No Kumo endpoint is configured (KUMO_RELATIONAL_URL).'],
    })
    expect(summary.output).toBeUndefined()
    const failedQuery = { ...receiptOf('structured_query'), status: 'failed' as const }
    expect(summarizeReceipt(failedQuery).details).toEqual([])
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
