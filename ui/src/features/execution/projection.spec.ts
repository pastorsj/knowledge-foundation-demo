// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import type { ExecutionEventV2 } from './contract'
import { buildExecutionGraphViewModel, toGraphEvent, toGraphProjection } from './graph'
import { describeRun, projectRun } from './projection'
import { fixtureEvents } from './test-utils/fixtures'

const withEvent = (index: number, patch: Partial<ExecutionEventV2>): ExecutionEventV2 => ({
  ...fixtureEvents[index],
  ...patch,
})

describe('projectRun', () => {
  it('reduces the golden run to its tool calls, model calls and tokens', () => {
    const run = projectRun(fixtureEvents)
    expect(run.status).toBe('completed')
    expect(run.endedAt).toBe(fixtureEvents.at(-1)!.occurredAt)
    expect(
      run.toolCalls.map(({ name, label, family, state, receiptIds }) => ({
        name,
        label,
        family,
        state,
        receipts: receiptIds.length,
      }))
    ).toEqual([
      {
        name: 'market_anomaly_scan',
        label: 'Market Anomaly Scan',
        family: 'market_analytics',
        state: 'completed',
        receipts: 1,
      },
      {
        name: 'retrieve_evidence',
        label: 'Unstructured Retrieval',
        family: 'unstructured_retrieval',
        state: 'completed',
        receipts: 1,
      },
    ])
    expect(run.modelCalls.map((call) => [call.servedModel, call.tier])).toEqual([
      ['nvidia/nemotron-3-ultra-550b-a55b', 'efficient'],
      ['gpt-6-sol', 'capable'],
    ])
    // The run's own usage wins over the sum of the model calls
    expect([run.inputTokens, run.outputTokens]).toEqual([103009, 3208])
  })

  it('shows a replay prefix as still running', () => {
    const run = projectRun(fixtureEvents.slice(0, 4))
    expect(run.status).toBe('running')
    expect(run.endedAt).toBeNull()
    expect(run.toolCalls.map((call) => call.state)).toEqual(['running', 'running'])
    expect(run.inputTokens).toBe(9120)
  })

  it('fails the run and a tool that reported an error', () => {
    const run = projectRun([
      fixtureEvents[0],
      fixtureEvents[2],
      withEvent(6, {
        display: { ...fixtureEvents[6].display, attributes: { reported_error: true } },
      }),
      withEvent(9, { eventKind: 'run.failed', state: 'failed' }),
    ])
    expect(run.status).toBe('failed')
    expect(run.toolCalls[0].state).toBe('failed')
  })

  it('keeps a failed receipt failed after the call reports it completed', () => {
    const failedReceipt = withEvent(4, { eventKind: 'tool.observed', state: 'failed' })
    const run = projectRun([fixtureEvents[0], fixtureEvents[2], failedReceipt, fixtureEvents[6]])
    expect(run.toolCalls[0]).toMatchObject({
      state: 'failed',
      receiptIds: failedReceipt.artifactRefs,
    })
    const events = [fixtureEvents[0], fixtureEvents[2], failedReceipt, fixtureEvents[6]].map(
      toGraphEvent
    )
    const graph = buildExecutionGraphViewModel({
      allEvents: events,
      visibleEvents: events,
      projection: toGraphProjection(run, events),
    })
    expect(graph.nodes.find((node) => node.id === 'market-anomaly-scan')!.state).toBe('failed')
  })

  it('ends the run with the job when no run event says how it ended', () => {
    const run = projectRun(fixtureEvents.slice(0, 4), 'interrupted')
    expect(run.status).toBe('cancelled')
    expect(run.endedAt).toBe(fixtureEvents[3].occurredAt)
    // The calls the cancel cut off did not finish
    expect(run.toolCalls.map((call) => call.state)).toEqual(['failed', 'failed'])
    expect(projectRun(fixtureEvents.slice(0, 4), 'running').status).toBe('running')
  })

  it('fails a completed run whose job failed, but keeps it completed after a late cancel', () => {
    expect(projectRun(fixtureEvents, 'failure').status).toBe('failed')
    expect(projectRun(fixtureEvents, 'interrupted').status).toBe('completed')
  })

  it('keeps a tool the registry does not know, under its own name', () => {
    const run = projectRun([withEvent(2, { toolName: 'web_search', toolServer: 'web' })])
    expect(run.toolCalls[0]).toMatchObject({
      name: 'web_search',
      label: 'web_search',
      family: null,
    })
    expect(run.toolCalls[0].tool).toBeUndefined()
  })

  it('describes a run in one line', () => {
    expect(describeRun(projectRun(fixtureEvents))).toBe(
      'completed · 2 tool calls · 2 model calls · 106,217 tokens'
    )
    expect(describeRun(projectRun([]))).toBe('waiting · 0 tool calls · 0 model calls')
  })
})
