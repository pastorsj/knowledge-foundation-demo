// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import type { ExecutionEventV2 } from '../contract'
import { projectRun } from '../projection'
import { fixtureEvents } from '../test-utils/fixtures'
import { toGraphEvent, toGraphProjection } from './graph-events'
import {
  buildExecutionGraphViewModel,
  buildExecutionNodeDetail,
  HERMES_EXECUTION_CANVAS_HEIGHT,
  HERMES_EXECUTION_CANVAS_WIDTH,
  isInspectableNode,
  type ExecutionGraphViewModel,
} from './graph-model'

/** The graph of the first `step` events of a run (all of them by default). */
const graphAt = (events: ExecutionEventV2[], step = events.length): ExecutionGraphViewModel => {
  const all = events.map(toGraphEvent)
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
  { failed = false, receipt = true } = {}
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
            artifactRefs: [`receipt:${invocationId}`],
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

describe('buildExecutionGraphViewModel', () => {
  it('draws the one fixed topology, whatever the run used', () => {
    const graph = graphAt(fixtureEvents)
    expect(graph.nodes).toHaveLength(25)
    expect(graph.groups.map((group) => group.id)).toEqual([
      'tool-control',
      'structured-data',
      'agent-utilities',
      'unstructured-data',
      'market-analytics',
    ])
    expect([graph.canvasWidth, graph.canvasHeight]).toEqual([
      HERMES_EXECUTION_CANVAS_WIDTH,
      HERMES_EXECUTION_CANVAS_HEIGHT,
    ])
    expect(graphAt(fixtureEvents, 0).nodes.map((node) => [node.id, node.x, node.y])).toEqual(
      graph.nodes.map((node) => [node.id, node.x, node.y])
    )
  })

  it('lights what the golden run used, once each, and leaves the rest unobserved', () => {
    const graph = graphAt(fixtureEvents)
    for (const id of [
      'business-question',
      'hermes-agent',
      'tool-call',
      'market-anomaly-scan',
      'retriever-tool',
      'unstructured-retrieval',
      'synthesis',
      'report-generation',
      'trusted-answer',
    ]) {
      expect(nodeOf(graph, id).state, id).toBe('completed')
    }
    for (const id of ['market-scan', 'nvidia-kumo', 'nvidia-ontology', 'skill-view']) {
      expect(nodeOf(graph, id).state, id).toBe('unobserved')
    }
    // Model calls belong to the agent's run: no call count on the agent
    expect(nodeOf(graph, 'hermes-agent').count).toBe(1)
    expect(nodeOf(graph, 'market-anomaly-scan').count).toBe(1)
    expect(nodeOf(graph, 'tool-call').count).toBe(2)
    expect(edgeOf(graph, 'question-hermes').state).toBe('completed')
    expect(edgeOf(graph, 'retriever-tool-result').state).toBe('completed')
    expect(edgeOf(graph, 'market-anomaly-scan-synthesis').state).toBe('completed')
    expect(edgeOf(graph, 'report-answer').state).toBe('completed')
    expect(edgeOf(graph, 'predict-kumo').state).toBe('unobserved')
  })

  it('replays: the call at the cursor runs, and what comes later is pending', () => {
    const graph = graphAt(fixtureEvents, 3)
    expect(nodeOf(graph, 'market-anomaly-scan')).toMatchObject({ state: 'running', current: true })
    expect(nodeOf(graph, 'retriever-tool').state).toBe('pending')
    expect(nodeOf(graph, 'trusted-answer').state).toBe('pending')
    expect(nodeOf(graph, 'market-scan').state).toBe('unobserved')
    expect(edgeOf(graph, 'retriever-tool-result').state).toBe('pending')
  })

  it('keeps a tool that succeeded on retry completed once the run ends', () => {
    const events = run(
      toolCall('call-1', 'market_scan', 'nvidia.market_analytics', {
        failed: true,
        receipt: false,
      }),
      toolCall('call-2', 'market_scan', 'nvidia.market_analytics')
    )
    expect(nodeOf(graphAt(events), 'market-scan')).toMatchObject({ state: 'completed', count: 2 })
    // Mid-replay it shows the attempt at the cursor
    expect(nodeOf(graphAt(events, 3), 'market-scan').state).toBe('failed')
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
    expect(nodeOf(graph, 'nvidia-kumo').state).toBe('unobserved')
  })

  it('draws a prediction through NVIDIA Kumo', () => {
    const graph = graphAt(run(toolCall('call-1', 'predict_asset_outcomes', 'nvidia.kumo')))
    expect(nodeOf(graph, 'structured-prediction').state).toBe('completed')
    expect(nodeOf(graph, 'nvidia-kumo').state).toBe('completed')
    expect(edgeOf(graph, 'predict-kumo').state).toBe('completed')
    expect(nodeOf(graph, 'nvidia-ontology').state).toBe('unobserved')
  })

  it('draws tools without a node of their own as Other Tools', () => {
    const graph = graphAt(
      run(
        toolCall('call-1', 'intraday_scan', 'nvidia.market_analytics'),
        toolCall('call-2', 'web_search', 'hermes.tool', { receipt: false })
      )
    )
    expect(nodeOf(graph, 'hermes-tools')).toMatchObject({ state: 'completed', count: 2 })
    expect(nodeOf(graph, 'market-scan').state).toBe('unobserved')
  })

  it('draws Hermes utilities as their own nodes', () => {
    const graph = graphAt(run(toolCall('call-1', 'skill_view', 'hermes.tool', { receipt: false })))
    expect(nodeOf(graph, 'skill-view').state).toBe('completed')
    expect(nodeOf(graph, 'hermes-tools').state).toBe('unobserved')
  })
})

describe('buildExecutionNodeDetail', () => {
  it('lists the calls behind a node with their evidence', () => {
    const events = fixtureEvents.map(toGraphEvent)
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
    expect(isInspectableNode('market-scan')).toBe(true)
    expect(isInspectableNode('hermes-agent')).toBe(true)
    expect(isInspectableNode('retriever-tool')).toBe(false)
    expect(isInspectableNode('synthesis')).toBe(false)
  })
})
