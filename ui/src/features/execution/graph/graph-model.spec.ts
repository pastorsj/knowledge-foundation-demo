// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import type { ExecutionEventV2, ReceiptV2 } from '../contract'
import { projectRun } from '../projection'
import { fixtureEvents, fixtureReceipts } from '../test-utils/fixtures'
import { toGraphEvent, toGraphProjection, type GraphReceipts } from './graph-events'
import {
  buildExecutionGraphViewModel,
  buildExecutionNodeDetail,
  HERMES_EXECUTION_CANVAS_HEIGHT,
  HERMES_EXECUTION_CANVAS_WIDTH,
  isInspectableNode,
  type ExecutionGraphViewModel,
} from './graph-model'

const RECEIPTS: GraphReceipts = Object.fromEntries(
  fixtureReceipts.map((receipt) => [receipt.receiptId, receipt])
)

/** The graph of the first `step` events of a run (all of them by default). */
const graphAt = (
  events: ExecutionEventV2[],
  step = events.length,
  receipts: GraphReceipts = RECEIPTS
): ExecutionGraphViewModel => {
  const all = events.map((event) => toGraphEvent(event, receipts))
  const visible = all.slice(0, step)
  const run = projectRun(events.slice(0, step))
  return buildExecutionGraphViewModel({
    allEvents: all,
    visibleEvents: visible,
    projection: toGraphProjection(run, visible),
  })
}
const nodeOf = (graph: ExecutionGraphViewModel, id: string) =>
  graph.nodes.find((node) => node.id === id)!
const edgeOf = (graph: ExecutionGraphViewModel, id: string) =>
  graph.edges.find((edge) => edge.id === id)!

/** A tool call of `toolName` as its start, receipt and end events. */
const toolCall = (
  invocationId: string,
  toolName: string,
  componentId: string,
  { failed = false, receipt = true, receiptId = `receipt:${invocationId}` } = {}
): ExecutionEventV2[] => {
  const base = { ...fixtureEvents[2], invocationId, toolName, componentId }
  return [
    { ...base, eventId: `${invocationId}:start` },
    ...(receipt
      ? [
          {
            ...fixtureEvents[4],
            invocationId,
            toolName,
            componentId,
            eventId: `${invocationId}:receipt`,
            artifactRefs: [receiptId],
          },
        ]
      : []),
    {
      ...fixtureEvents[6],
      invocationId,
      toolName,
      componentId,
      eventId: `${invocationId}:end`,
      state: failed ? 'failed' : 'completed',
    },
  ]
}
const run = (...calls: ExecutionEventV2[][]): ExecutionEventV2[] => [
  fixtureEvents[0],
  ...calls.flat(),
  fixtureEvents[9],
]

/** The fixture retrieval that ran without a rerank model */
const unreranked = fixtureReceipts.find(
  (receipt: ReceiptV2) =>
    receipt.artifactKind === 'retrieval_evidence' && !receipt.content?.models.rerank
)!

describe('buildExecutionGraphViewModel', () => {
  it('draws the one fixed topology, whatever the run used', () => {
    const graph = graphAt(fixtureEvents)
    expect(graph.nodes).toHaveLength(25)
    expect(graph.groups.map((group) => [group.id, group.label])).toEqual([
      ['tool-control', 'Tool Control'],
      ['structured-data', 'Structured Data'],
      ['agent-utilities', 'Agent Utilities'],
      ['unstructured-data', 'Documents'],
    ])
    expect([graph.canvasWidth, graph.canvasHeight]).toEqual([
      HERMES_EXECUTION_CANVAS_WIDTH,
      HERMES_EXECUTION_CANVAS_HEIGHT,
    ])
    expect(graphAt(fixtureEvents, 0).nodes.map((node) => [node.id, node.x, node.y])).toEqual(
      graph.nodes.map((node) => [node.id, node.x, node.y])
    )
    // Every node sits inside the canvas, and no two overlap
    for (const node of graph.nodes) {
      expect(node.x + (node.width ?? 180), node.id).toBeLessThanOrEqual(graph.canvasWidth)
      expect(node.y + (node.height ?? 80), node.id).toBeLessThanOrEqual(graph.canvasHeight)
    }
    for (const a of graph.nodes) {
      for (const b of graph.nodes) {
        if (a === b) continue
        const apart =
          a.x + (a.width ?? 180) <= b.x ||
          b.x + (b.width ?? 180) <= a.x ||
          a.y + (a.height ?? 80) <= b.y ||
          b.y + (b.height ?? 80) <= a.y
        expect(apart, `${a.id} / ${b.id}`).toBe(true)
      }
    }
  })

  it('lights what the golden run used: a DuckDB table query and a reranked retrieval', () => {
    const graph = graphAt(fixtureEvents)
    for (const id of [
      'business-question',
      'hermes-agent',
      'tool-call',
      'tables-tool',
      'structured-retrieval',
      'structured-database',
      'retriever-tool',
      'nemotron-parse',
      'nemotron-embed',
      'milvus',
      'nemotron-rerank',
      'unstructured-retrieval',
      'synthesis',
      'report-generation',
      'trusted-answer',
    ]) {
      expect(nodeOf(graph, id).state, id).toBe('completed')
    }
    for (const id of ['nvidia-kumo', 'nvidia-ontology', 'ontology-tool', 'skill-view']) {
      expect(nodeOf(graph, id).state, id).toBe('unobserved')
    }
    // Model calls belong to the agent's run: no call count on the agent
    expect(nodeOf(graph, 'hermes-agent').count).toBe(1)
    expect(nodeOf(graph, 'tables-tool').count).toBe(1)
    expect(nodeOf(graph, 'tool-call').count).toBe(2)
    for (const id of [
      'question-hermes',
      'tool-call-tables',
      'tables-query',
      'query-source',
      'query-synthesis',
      'tool-call-retriever',
      'retriever-parse',
      'parse-embed',
      'embed-milvus',
      'milvus-rerank',
      'rerank-evidence',
      'retrieve-synthesis',
      'report-answer',
    ]) {
      expect(edgeOf(graph, id).state, id).toBe('completed')
    }
    // A reranked retrieval never takes the vector-order bypass
    expect(edgeOf(graph, 'milvus-evidence').state).toBe('unobserved')
    expect(edgeOf(graph, 'predict-kumo').state).toBe('unobserved')
    expect(edgeOf(graph, 'ontology-query').state).toBe('unobserved')
  })

  it('draws a retrieval without a rerank model in vector order, around Nemotron Rerank', () => {
    const graph = graphAt(
      run(
        toolCall('call-1', 'retrieve_evidence', 'milvus.retrieval', {
          receiptId: unreranked.receiptId,
        })
      )
    )
    expect(nodeOf(graph, 'milvus').state).toBe('completed')
    expect(nodeOf(graph, 'nemotron-rerank').state).toBe('unobserved')
    expect(edgeOf(graph, 'milvus-evidence').state).toBe('completed')
    expect(edgeOf(graph, 'milvus-rerank').state).toBe('unobserved')
    expect(edgeOf(graph, 'rerank-evidence').state).toBe('unobserved')
  })

  it('lights Nemotron Rerank only once the receipt that names it is known', () => {
    const graph = graphAt(fixtureEvents, fixtureEvents.length, {})
    expect(nodeOf(graph, 'nemotron-rerank').state).toBe('unobserved')
    expect(edgeOf(graph, 'milvus-evidence').state).toBe('completed')
  })

  it('replays: the call at the cursor runs, and what comes later is pending', () => {
    const graph = graphAt(fixtureEvents, 3)
    expect(nodeOf(graph, 'tables-tool')).toMatchObject({ state: 'running', current: true })
    expect(nodeOf(graph, 'structured-retrieval').state).toBe('running')
    expect(nodeOf(graph, 'retriever-tool').state).toBe('pending')
    expect(nodeOf(graph, 'nemotron-rerank').state).toBe('pending')
    expect(nodeOf(graph, 'trusted-answer').state).toBe('pending')
    expect(nodeOf(graph, 'nvidia-kumo').state).toBe('unobserved')
    expect(edgeOf(graph, 'rerank-evidence').state).toBe('pending')
  })

  it('keeps a tool that succeeded on retry completed once the run ends', () => {
    const events = run(
      toolCall('call-1', 'query_tables', 'duckdb.tables', { failed: true, receipt: false }),
      toolCall('call-2', 'query_tables', 'duckdb.tables')
    )
    expect(nodeOf(graphAt(events), 'tables-tool')).toMatchObject({ state: 'completed', count: 2 })
    // Mid-replay it shows the attempt at the cursor
    expect(nodeOf(graphAt(events, 3), 'tables-tool').state).toBe('failed')
  })

  it('draws Auto Ontology through the ontology, SQL and the database', () => {
    const graph = graphAt(run(toolCall('call-1', 'ask_question', 'nvidia.ontology')))
    for (const id of [
      'ontology-tool',
      'nvidia-ontology',
      'structured-retrieval',
      'structured-database',
    ]) {
      expect(nodeOf(graph, id).state, id).toBe('completed')
    }
    expect(edgeOf(graph, 'ontology-tool-resource').state).toBe('completed')
    expect(edgeOf(graph, 'query-source').state).toBe('completed')
    expect(nodeOf(graph, 'tables-tool').state).toBe('unobserved')
    expect(edgeOf(graph, 'tables-query').state).toBe('unobserved')
    expect(nodeOf(graph, 'nvidia-kumo').state).toBe('unobserved')
  })

  it('draws a prediction through its tool, NVIDIA Kumo and the tables it reads', () => {
    const graph = graphAt(run(toolCall('call-1', 'predict', 'nvidia.kumo')))
    for (const id of [
      'prediction-tool',
      'structured-prediction',
      'nvidia-kumo',
      'structured-database',
    ]) {
      expect(nodeOf(graph, id).state, id).toBe('completed')
    }
    expect(edgeOf(graph, 'tool-call-prediction').state).toBe('completed')
    expect(edgeOf(graph, 'predict-kumo').state).toBe('completed')
    expect(edgeOf(graph, 'source-kumo').state).toBe('completed')
    expect(nodeOf(graph, 'nvidia-ontology').state).toBe('unobserved')
  })

  it('draws tools without a node of their own as Other Tools', () => {
    const graph = graphAt(
      run(
        toolCall('call-1', 'list_files', 'hermes.tool', { receipt: false }),
        toolCall('call-2', 'web_search', 'hermes.tool', { receipt: false })
      )
    )
    expect(nodeOf(graph, 'hermes-tools')).toMatchObject({ state: 'completed', count: 2 })
    expect(nodeOf(graph, 'tables-tool').state).toBe('unobserved')
  })

  it('draws Hermes utilities as their own nodes', () => {
    const graph = graphAt(run(toolCall('call-1', 'skill_view', 'hermes.tool', { receipt: false })))
    expect(nodeOf(graph, 'skill-view').state).toBe('completed')
    expect(nodeOf(graph, 'hermes-tools').state).toBe('unobserved')
  })
})

describe('buildExecutionNodeDetail', () => {
  it('lists the calls behind a node with their evidence', () => {
    const events = fixtureEvents.map((event) => toGraphEvent(event, RECEIPTS))
    const graph = graphAt(fixtureEvents)
    const detail = buildExecutionNodeDetail(
      'unstructured-retrieval',
      graph,
      toGraphProjection(projectRun(fixtureEvents), events)
    )
    expect(detail).toMatchObject({
      label: 'Unstructured Retrieval',
      observed: true,
      artifactRefs: fixtureEvents[5].artifactRefs,
    })
    expect(detail.invocations.map((call) => call.name)).toEqual(['retrieve_evidence'])
  })

  it('opens only capability, resource and agent nodes', () => {
    expect(isInspectableNode('structured-retrieval')).toBe(true)
    expect(isInspectableNode('structured-database')).toBe(true)
    expect(isInspectableNode('hermes-agent')).toBe(true)
    expect(isInspectableNode('retriever-tool')).toBe(false)
    expect(isInspectableNode('milvus')).toBe(false)
    expect(isInspectableNode('synthesis')).toBe(false)
  })
})
