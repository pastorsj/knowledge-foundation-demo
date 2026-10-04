// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The golden contract fixtures (contracts/fixtures) and the e2e recordings
 * bundle, for specs. Read from disk at test time: the UI image is built from
 * ui/ alone, so nothing under contracts/ may be imported.
 */

import { readFileSync } from 'node:fs'
import path from 'node:path'
import type { ArtifactKind, ExecutionEventV2, ReceiptV2 } from '../contract'

const readJson = (...parts: string[]): unknown =>
  JSON.parse(readFileSync(path.resolve(process.cwd(), ...parts), 'utf8'))

/** One recorded run: a DuckDB table query and a document retrieval, plus two model calls. */
export const fixtureEvents = readJson(
  '../contracts/fixtures/execution-events.json'
) as ExecutionEventV2[]

/**
 * One completed receipt per artifactKind (and per structured query tool), a retrieval without a
 * rerank model, and a failed prediction.
 */
export const fixtureReceipts = readJson('../contracts/fixtures/receipts.json') as ReceiptV2[]

export const receiptOf = <K extends ArtifactKind>(
  kind: K,
  status: ReceiptV2['status'] = 'completed'
): Extract<ReceiptV2, { artifactKind: K }> =>
  fixtureReceipts.find(
    (receipt) => receipt.artifactKind === kind && receipt.status === status
  ) as Extract<ReceiptV2, { artifactKind: K }>

/** The first receipt of a registered tool, by its MCP name (`ask_question`, `query_tables`, …). */
export const receiptOfTool = (
  toolId: string,
  status: ReceiptV2['status'] = 'completed'
): ReceiptV2 =>
  fixtureReceipts.find(
    (receipt) => receipt.toolName.endsWith(`__${toolId}`) && receipt.status === status
  )!

export const readRecording = (file: string): unknown =>
  readJson('e2e/fixtures/packs/retail/recordings', file)

/**
 * The events the API records when it publishes the fixture run's answer: response formatted,
 * citations resolved and the run's metrics, 100 ms apart after the run completes.
 */
export const publicationEvents = (
  resolution: Record<string, unknown>,
  metrics: Record<string, unknown>
): ExecutionEventV2[] => {
  const last = fixtureEvents[fixtureEvents.length - 1]
  const steps: Array<[string, string, Record<string, unknown>]> = [
    ['report.completed', 'Response formatted', {}],
    ['report.reference_resolution', 'Citations resolved', resolution],
    ['report.metrics', 'Run metrics available', metrics],
  ]
  return steps.map(([eventKind, label, attributes], index) => ({
    ...last,
    eventId: `publication-${index}`,
    cursor: (last.cursor ?? 0) + index + 1,
    eventKind,
    occurredAt: new Date(Date.parse(last.occurredAt) + 100 * (index + 1)).toISOString(),
    display: { label, summary: null, attributes, inspectable: false },
    provenance: { ...last.provenance, sourceSystem: 'demo-api', sourceEventKind: eventKind },
  })) as ExecutionEventV2[]
}
