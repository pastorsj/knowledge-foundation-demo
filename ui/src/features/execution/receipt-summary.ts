// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * One receipt as the execution inspectors describe it: a title, a one-line
 * summary, detail chips, the statement it ran (SQL, PQL or a search query)
 * and its bounded output (rows or passages). The API has already validated
 * and bounded every receipt; this only chooses what to show.
 */

import type {
  EntityPrediction,
  ReceiptV2,
  RetrievalEvidence,
  StructuredPrediction,
  StructuredQuery,
} from './contract'

export interface ReceiptSummary {
  title: string
  summary: string
  details: string[]
  notices?: string[]
  statement?: {
    label: string
    language: 'sql' | 'pql' | 'json' | 'text'
    value: string
    truncated: boolean
  }
  output?: ReceiptOutput
}

export type ReceiptOutput =
  | {
      kind: 'table'
      label: 'Query result' | 'Prediction result'
      columns: string[]
      rows: string[][]
      displayedCount: number
      sourceCount?: number
      truncated: boolean
    }
  | {
      kind: 'passages'
      label: 'Retrieved passages'
      passages: Array<{ source: string; excerpt: string; metadata: string[] }>
      displayedCount: number
      sourceCount?: number
      truncated: boolean
    }

type Row = Record<string, unknown>

const MAX_ROWS = 25
const MAX_COLUMNS = 40
const MAX_PASSAGES = 10

const boundedText = (value: unknown, maxLength = 420): string | undefined => {
  if (typeof value !== 'string' || !value.trim()) return undefined
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > maxLength ? `${text.slice(0, maxLength - 1).trimEnd()}…` : text
}

const statementText = (value: string | null | undefined, maxLength: number) => {
  const text = value?.trim()
  return text ? { value: text.slice(0, maxLength), truncated: text.length > maxLength } : undefined
}

const plural = (value: number, singular: string, pluralForm = `${singular}s`): string =>
  `${value} ${value === 1 ? singular : pluralForm}`

const cellText = (value: unknown): string => {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return boundedText(value, 1_000) ?? '—'
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return boundedText(JSON.stringify(value), 1_000) ?? '—'
}

const detail = (label: string, value: unknown): string[] =>
  value === undefined || value === null || value === '' ? [] : [`${label}: ${value}`]

const tableOutput = (
  rows: readonly Row[],
  label: Extract<ReceiptOutput, { kind: 'table' }>['label'],
  sourceCount: number | undefined,
  truncated: boolean
): ReceiptOutput => {
  const shown = rows.slice(0, MAX_ROWS)
  const columns = [...new Set(shown.flatMap((row) => Object.keys(row)))].slice(0, MAX_COLUMNS)
  return {
    kind: 'table',
    label,
    columns,
    rows: shown.map((row) => columns.map((column) => cellText(row[column]))),
    displayedCount: rows.length,
    sourceCount,
    truncated,
  }
}

const countSummary = (displayed: number, source: number | undefined, noun: string): string => {
  const returned = `${plural(displayed, noun)} returned`
  return source === undefined || source === displayed
    ? `${returned}.`
    : `${returned} from ${plural(source, 'source row')}.`
}

const structuredQuery = (content: StructuredQuery): ReceiptSummary => {
  const sql = statementText(content.sql, 12_000)
  return {
    title: 'Structured result',
    summary: countSummary(content.rows.length, content.sourceRowCount, 'row'),
    details: [`Database: ${content.databaseName}`],
    statement: sql && { label: 'Generated SQL', language: 'sql', ...sql },
    output: tableOutput(
      content.rows,
      'Query result',
      content.sourceRowCount,
      content.truncated || content.sourceRowCount > content.rows.length
    ),
  }
}

/** Most likely first: by probability (a binary task or a class score), else by predicted value. */
const byScore = (a: EntityPrediction, b: EntityPrediction): number =>
  (b.probability ?? b.value ?? -Infinity) - (a.probability ?? a.value ?? -Infinity)

/**
 * A prediction in Kumo's output columns, as the original UI showed them, the most likely entity
 * first: a binary task's TRUE_PROB with its complement FALSE_PROB and PREDICTION (whether TRUE_PROB
 * is the larger), a multiclass task's CLASS with its SCORE, a regression's predicted value. Every
 * row carries the run's ANCHOR_TIMESTAMP when the receipt has one.
 */
const kumoRows = (content: StructuredPrediction): Row[] =>
  [...content.rows].sort(byScore).map((row) => {
    const anchor = content.anchorTime ? { ANCHOR_TIMESTAMP: content.anchorTime } : {}
    if (row.label !== null) {
      return { ...anchor, ENTITY: row.entityId, CLASS: row.label, SCORE: row.probability }
    }
    if (row.probability !== null) {
      return {
        ...anchor,
        ENTITY: row.entityId,
        FALSE_PROB: 1 - row.probability,
        PREDICTION: row.probability > 0.5,
        TRUE_PROB: row.probability,
      }
    }
    return { ...anchor, ENTITY: row.entityId, PREDICTION: row.value }
  })

const structuredPrediction = (content: StructuredPrediction): ReceiptSummary => {
  const pql = statementText(content.pql, 8_000)
  return {
    title: 'Prediction result',
    summary: content.available
      ? countSummary(content.rows.length, undefined, 'prediction')
      : (content.reason ?? 'The prediction could not run.'),
    details: [
      `Source: ${content.sourceId}`,
      ...detail('Template', content.templateId),
      ...detail('Task', content.taskType?.replaceAll('_', ' ')),
      ...detail('Entities', content.entityTable),
      ...detail('Anchor time', content.anchorTime),
      ...(content.horizon ? [`Horizon: ${content.horizon.value} ${content.horizon.unit}`] : []),
      ...detail('Model', content.model),
    ],
    statement: pql && { label: 'Generated PQL', language: 'pql', ...pql },
    output: content.available
      ? tableOutput(kumoRows(content), 'Prediction result', undefined, false)
      : undefined,
  }
}

const retrieval = (content: RetrievalEvidence): ReceiptSummary => {
  const candidates = Object.values(content.candidateCounts).reduce((sum, n) => sum + n, 0)
  const searchParameters = Object.entries({
    ...content.index.params,
    ...content.index.searchParams,
  })
  const reranked = Boolean(content.models.rerank)
  const timings: Array<[string, number]> = [
    ['Embedding', content.timings.embedMs],
    ['Vector search', content.timings.searchMs],
    ...(reranked ? [['Reranking', content.timings.rerankMs] as [string, number]] : []),
    ['Total retrieval', content.timings.totalMs],
  ]
  const query = statementText(content.query, 1_000)
  return {
    title: 'Unstructured retrieval result',
    summary: `${plural(content.hits.length, 'passage')} displayed from ${plural(candidates, 'candidate')}.`,
    details: [
      `Sources: ${content.sourceIds.join(', ')}`,
      `Collection: ${content.collection}`,
      `Collection version: ${content.collectionVersion}`,
      // Milvus names its GPU indexes GPU_*; the rest run on the CPU
      `Vector index: ${content.index.type} (${content.index.type.startsWith('GPU_') ? 'GPU' : 'CPU'})`,
      `Vector metric: ${content.index.metric}`,
      ...(searchParameters.length
        ? [
            `Search parameters: ${searchParameters.map(([name, value]) => `${name} ${value}`).join(' · ')}`,
          ]
        : []),
      reranked ? 'Ranking: reranker' : 'Ranking: vector score (no rerank model)',
      `Embedding model: ${content.models.embed}`,
      ...(reranked ? [`Reranker model: ${content.models.rerank}`] : []),
      ...timings.map(([label, ms]) => `${label}: ${ms.toFixed(1)} ms`),
    ],
    statement: query && { label: 'Search query', language: 'text', ...query },
    output: {
      kind: 'passages',
      label: 'Retrieved passages',
      passages: content.hits.slice(0, MAX_PASSAGES).map((hit) => ({
        source: boundedText(hit.title, 320) ?? hit.documentId,
        excerpt: boundedText(hit.snippet, 1_500) ?? '',
        metadata: [
          `Rank ${hit.rank}`,
          `Source: ${hit.sourceId}`,
          `Document: ${hit.documentId}`,
          `Chunk: ${hit.chunkId}`,
          // As the original listed it: a rerank logit only when it is not negative
          ...(reranked && hit.score >= 0 ? [`Rerank score: ${hit.score}`] : []),
          `Vector score: ${hit.vectorScore}`,
          ...(hit.publishedAt ? [`Published: ${hit.publishedAt}`] : []),
          ...(typeof hit.metadata?.parser === 'string' && hit.metadata.parser.trim()
            ? [`Parser: ${boundedText(hit.metadata.parser, 80)}`]
            : []),
          ...(typeof hit.metadata?.citation === 'string' && hit.metadata.citation.trim()
            ? [`Citation: ${boundedText(hit.metadata.citation, 400)}`]
            : []),
          ...(hit.url ? [`Source URL: ${hit.url}`] : []),
        ],
      })),
      displayedCount: content.hits.length,
      sourceCount: candidates,
      truncated: candidates > content.hits.length,
    },
  }
}

/** Summarizes one receipt for the inspectors (and the timeline). */
export const summarizeReceipt = (receipt: ReceiptV2): ReceiptSummary => {
  if (receipt.status === 'failed' || !receipt.content) {
    const prediction = receipt.artifactKind === 'structured_prediction' ? receipt.content : null
    return {
      title: 'Tool result',
      summary: 'The tool call ended with a failure.',
      details: prediction ? [`Source: ${prediction.sourceId}`] : [],
      notices: [
        ...(receipt.errorSummary ? [receipt.errorSummary] : []),
        ...(prediction?.reason && prediction.reason !== receipt.errorSummary
          ? [prediction.reason]
          : []),
      ],
    }
  }
  switch (receipt.artifactKind) {
    case 'structured_query':
      return structuredQuery(receipt.content)
    case 'structured_prediction':
      return structuredPrediction(receipt.content)
    case 'retrieval_evidence':
      return retrieval(receipt.content)
  }
}

/** "8 rows", "3 passages of 32" */
export const receiptOutputCount = (output: ReceiptOutput): string => {
  const noun = output.kind === 'table' ? 'row' : 'passage'
  const displayed = `${output.displayedCount} ${noun}${output.displayedCount === 1 ? '' : 's'}`
  return output.sourceCount === undefined || output.sourceCount === output.displayedCount
    ? displayed
    : `${displayed} of ${output.sourceCount}`
}
