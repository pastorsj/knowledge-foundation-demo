// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import type { ExecutionEventV2 } from '../contract'
import { fixtureEvents, publicationEvents } from '../test-utils/fixtures'
import {
  buildActionTimeline,
  buildThinkingActivity,
  formatTimelineDuration,
} from './activity-model'

let sequence = 0
const at = (seconds: number) => new Date(Date.UTC(2026, 8, 28, 5, 0, seconds)).toISOString()

/** A `tool.*` event of a Hermes or registered tool. */
const tool = (
  eventKind: string,
  toolName: string,
  invocationId: string,
  seconds: number,
  extra: Partial<ExecutionEventV2> & { summary?: string; error?: boolean } = {}
): ExecutionEventV2 => {
  const { summary = null, error, ...rest } = extra
  sequence += 1
  return {
    ...fixtureEvents[2],
    eventId: `event-${sequence}`,
    cursor: sequence,
    eventKind,
    state: eventKind === 'tool.started' ? 'started' : 'completed',
    toolName,
    invocationId,
    occurredAt: at(seconds),
    artifactRefs: [],
    display: {
      label: `${toolName} ${eventKind === 'tool.started' ? 'started' : 'completed'}`,
      summary,
      attributes: error === undefined ? {} : { reported_error: error },
      inspectable: false,
    },
    ...rest,
  }
}

const run = (eventKind: string, seconds: number, state: ExecutionEventV2['state']) => {
  sequence += 1
  return {
    ...fixtureEvents[0],
    eventId: `event-${sequence}`,
    cursor: sequence,
    eventKind,
    state,
    occurredAt: at(seconds),
    display: { ...fixtureEvents[0].display, label: eventKind, summary: null },
  }
}

describe('Thinking', () => {
  test('narrates a recorded run: request, each tool call, evidence and the answer', () => {
    const items = buildThinkingActivity(fixtureEvents)

    expect(items.map((item) => [item.label, item.status])).toEqual([
      ['Request accepted', 'completed'],
      ['Table Query', 'completed'],
      ['Document Retrieval', 'completed'],
      ['Answer ready', 'completed'],
    ])
    expect(items[1].description).toBe(
      'Running one read-only SQL query over the selected tables in DuckDB.'
    )
    expect(items[2].description).toBe('Searching, ranking, and selecting supporting passages.')
  })

  test('names skills and Hermes tools, numbers repeated calls and marks a failure', () => {
    const events = [
      run('run.created', 0, 'started'),
      tool('tool.started', 'skill_view', 'skill-1', 1, { summary: 'querying-tables' }),
      tool('tool.completed', 'skill_view', 'skill-1', 1),
      tool('tool.started', 'tool_describe', 'describe-1', 2),
      tool('tool.completed', 'tool_describe', 'describe-1', 2),
      run('reasoning.available', 3, 'progress'),
      run('reasoning.available', 3, 'progress'),
      tool('tool.started', 'query_tables', 'sql-1', 4),
      tool('tool.completed', 'query_tables', 'sql-1', 5),
      tool('tool.started', 'query_tables', 'sql-2', 6),
      tool('tool.completed', 'query_tables', 'sql-2', 7, { error: true }),
      tool('tool.started', 'ask_question', 'ontology-1', 7),
      tool('tool.completed', 'ask_question', 'ontology-1', 7),
      run('run.failed', 8, 'failed'),
    ]

    const items = buildThinkingActivity(events)

    expect(items.map((item) => [item.label, item.status])).toEqual([
      ['Request accepted', 'completed'],
      ['Reading skill: querying-tables', 'completed'],
      ['Inspecting tool definitions', 'completed'],
      ['Evaluating evidence', 'completed'],
      ['Table Query · Call 1', 'completed'],
      ['Table Query · Call 2', 'failed'],
      // As the original named an Auto Ontology call
      ['Auto Ontology Text-to-SQL', 'completed'],
      ['Run stopped', 'failed'],
    ])
    expect(items[1].description).toBe(
      'Loading the querying-tables workflow instructions for this run.'
    )
    expect(items[5].failureDetail).toBe('This observed activity ended with a failure.')
  })

  test('shows a call in progress while the run streams', () => {
    const events = [run('run.created', 0, 'started'), tool('tool.started', 'predict', 's', 1)]

    expect(buildThinkingActivity(events).map((item) => item.status)).toEqual([
      'completed',
      'running',
    ])
  })
})

describe('Timeline', () => {
  test('names each action by its service: DuckDB, Auto Ontology, NVIDIA Kumo, Milvus', () => {
    const model = buildActionTimeline([
      run('run.created', 0, 'started'),
      tool('tool.started', 'ask_question', 'ontology', 1),
      tool('tool.completed', 'ask_question', 'ontology', 2),
      tool('tool.started', 'predict', 'kumo', 3),
      tool('tool.completed', 'predict', 'kumo', 4),
      run('run.completed', 5, 'completed'),
    ])!
    expect(model.items.slice(0, 2).map((item) => [item.label, item.service])).toEqual([
      ['Auto Ontology Text-to-SQL', 'Auto Ontology'],
      ['Structured Prediction', 'NVIDIA Kumo'],
    ])
  })

  test('draws observed spans and milestones with the run totals', () => {
    const model = buildActionTimeline(fixtureEvents)!

    expect(model.status).toBe('completed')
    expect(formatTimelineDuration(model.durationMs)).toBe('54 s')
    expect(model.metrics).toMatchObject({
      toolCallCount: 2,
      totalTokens: 106_217,
      inputTokens: 103_009,
      outputTokens: 3_208,
    })
    expect(formatTimelineDuration(model.metrics.knownToolDurationMs)).toBe('5 s')
    expect(model.items.map((item) => [item.label, item.service, item.kind, item.timing])).toEqual([
      ['Table Query', 'DuckDB', 'span', 'observed'],
      ['Document Retrieval', 'Milvus + NVIDIA Nemotron', 'span', 'observed'],
      ['Answer ready', 'Response pipeline', 'milestone', 'point'],
    ])
    expect(model.items[0].receiptId).toBe(fixtureEvents[4].artifactRefs[0])
    expect(formatTimelineDuration(model.items[0].startOffsetMs)).toBe('12 s')
  })

  test('resolves citations as a milestone, and spans the run its metrics report', () => {
    const model = buildActionTimeline([
      ...fixtureEvents,
      ...publicationEvents(
        { status: 'reference_ids_resolved', total_citations: 1 },
        { wall_duration_ms: 90_000, tool_call_count: 4, known_tool_duration_ms: 7_000 }
      ),
    ])!

    expect(model.items.slice(-2).map((item) => [item.label, item.service])).toEqual([
      ['Answer ready', 'Response pipeline'],
      ['Citations resolved', 'Response pipeline'],
    ])
    expect(formatTimelineDuration(model.durationMs)).toBe('1m 30s')
    expect(model.metrics).toMatchObject({
      wallDurationMs: 90_000,
      toolCallCount: 4,
      knownToolDurationMs: 7_000,
    })
  })

  test('marks a start without an end incomplete, and a failed call failed', () => {
    const model = buildActionTimeline([
      run('run.created', 0, 'started'),
      tool('tool.started', 'retrieve_evidence', 'open', 1),
      tool('tool.started', 'predict', 'bad', 2),
      tool('tool.completed', 'predict', 'bad', 3, { error: true }),
      run('run.failed', 10, 'failed'),
    ])!

    expect(model.status).toBe('failed')
    const [open, bad] = model.items
    expect([open.timing, open.status, open.durationMs]).toEqual(['incomplete', 'incomplete', 9_000])
    expect([bad.status, bad.failureDetail]).toEqual([
      'failed',
      'This observed activity ended with a failure.',
    ])
  })

  test('formats durations the way the chart axis reads them', () => {
    expect([null, 0, 400, 7_400, 64_000].map((value) => formatTimelineDuration(value))).toEqual([
      'Unavailable',
      '0 s',
      '<1 s',
      '7 s',
      '1m 04s',
    ])
  })
})
