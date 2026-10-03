// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The graph's view of a run. The execution graph lights nodes by the
 * component, tool and resources behind each event, so this module reads each
 * `execution.v2` event (and the run projection) as those facts once, and the
 * graph model never looks at the wire format.
 */

import type { ExecutionEventV2, ReceiptV2 } from '../contract'
import type { RunProjection, RunStatus } from '../projection'
import { toolFor, type Tool } from '../registry'

/** What ran: the agent, a Hermes utility tool, or a capability behind a registered tool. */
export type GraphComponent =
  | 'agent'
  | 'tool'
  | 'ontology'
  | 'structured_retrieval'
  | 'structured_prediction'
  | 'unstructured_retrieval'

/** A service a call is known to have used. */
export type GraphResource =
  | 'structured_database'
  | 'nvidia_kumo'
  | 'nvidia_ontology'
  | 'nemotron_parse'
  | 'nemotron_embed'
  | 'milvus'
  | 'nemotron_rerank'

export type GraphEventKind =
  | 'run.started'
  | 'run.completed'
  | 'run.failed'
  | 'invocation.started'
  | 'invocation.completed'
  | 'invocation.failed'
  | 'artifact.available'

export interface GraphEvent {
  eventId: string
  kind: GraphEventKind
  /** The `execution.v2` eventKind it came from: run.created, run.heartbeat, llm.call, … */
  sourceKind: string
  component: GraphComponent
  /** The registered tool id, or the reported name of a tool the registry does not know */
  toolName: string | null
  invocationId: string | null
  parentInvocationId: string | null
  observedResources: GraphResource[]
}

export type GraphRunStatus = 'idle' | 'running' | 'completed' | 'failed'
export type GraphInvocationStatus = 'running' | 'completed' | 'failed'

export interface GraphInvocation {
  invocationId: string
  component: GraphComponent
  toolName: string | null
  status: GraphInvocationStatus
  artifactRefs: string[]
  observedResources: GraphResource[]
}

export interface GraphProjection {
  status: GraphRunStatus
  invocations: GraphInvocation[]
  answerAvailable: boolean
}

type Server = Tool['server']

/**
 * The capability behind a registered tool's calls, by its MCP server (the API's componentId is per
 * server too); its receipt (`artifact.available`) names the result. `query_tables` and Auto
 * Ontology's `ask_question` share a family but not a component: one runs SQL on DuckDB itself, the
 * other grounds the question in its ontology first.
 */
const CALL_COMPONENT: Record<Server, { call: GraphComponent; result: GraphComponent }> = {
  retrieval: { call: 'unstructured_retrieval', result: 'unstructured_retrieval' },
  tables: { call: 'structured_retrieval', result: 'structured_retrieval' },
  // Auto Ontology answers from the structured database: the call grounds, its receipt holds rows
  auto_ontology: { call: 'ontology', result: 'structured_retrieval' },
  prediction: { call: 'structured_prediction', result: 'structured_prediction' },
}

/** The documents path every retrieval searches: parsed, embedded and indexed at ingest. */
const DOCUMENTS: GraphResource[] = ['nemotron_parse', 'nemotron_embed', 'milvus']

const CALL_RESOURCES: Record<Server, { call: GraphResource[]; result: GraphResource[] }> = {
  retrieval: { call: DOCUMENTS, result: DOCUMENTS },
  tables: { call: ['structured_database'], result: ['structured_database'] },
  auto_ontology: {
    call: ['nvidia_ontology'],
    result: ['structured_database', 'nvidia_ontology'],
  },
  prediction: {
    call: ['nvidia_kumo'],
    result: ['structured_database', 'nvidia_kumo'],
  },
}

/** The receipts of a run, by id, for the facts only a result knows (a retrieval's rerank model). */
export type GraphReceipts = Readonly<Record<string, ReceiptV2 | undefined>>

/** Nemotron Rerank lights only when a retrieval's receipt names its rerank model. */
const reranked = (event: ExecutionEventV2, receipts: GraphReceipts): boolean =>
  event.artifactRefs.some((ref) => {
    const receipt = receipts[ref]
    return receipt?.artifactKind === 'retrieval_evidence' && Boolean(receipt.content?.models.rerank)
  })

const RUN_STATUS: Record<RunStatus, GraphRunStatus> = {
  waiting: 'idle',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
  cancelled: 'failed',
}

const failed = (event: ExecutionEventV2): boolean =>
  event.state === 'failed' ||
  event.state === 'cancelled' ||
  event.display.attributes.reported_error === true

const eventKind = (event: ExecutionEventV2): GraphEventKind => {
  const kind = event.eventKind
  if (kind === 'artifact.available') return 'artifact.available'
  if (kind.startsWith('tool.')) {
    if (event.state === 'started' || event.state === 'progress') return 'invocation.started'
    return failed(event) ? 'invocation.failed' : 'invocation.completed'
  }
  // Publishing the answer (response formatted, citations resolved, run metrics) completes the run too
  if (event.state === 'completed' && (kind.startsWith('run.') || kind.startsWith('report.'))) {
    return 'run.completed'
  }
  if (event.state === 'failed' || event.state === 'cancelled') return 'run.failed'
  // The agent is at work: heartbeats, reasoning, model calls and other runtime events
  return 'run.started'
}

/** Reads one `execution.v2` event as the facts the graph lights nodes by. */
export const toGraphEvent = (event: ExecutionEventV2, receipts: GraphReceipts = {}): GraphEvent => {
  const kind = eventKind(event)
  const toolEvent = kind.startsWith('invocation.') || kind === 'artifact.available'
  const tool = toolEvent ? toolFor(event.toolName) : undefined
  const result = kind === 'artifact.available'
  const component: GraphComponent = !toolEvent
    ? 'agent'
    : tool
      ? CALL_COMPONENT[tool.server][result ? 'result' : 'call']
      : 'tool'
  const resources: GraphResource[] = tool
    ? [...CALL_RESOURCES[tool.server][result ? 'result' : 'call']]
    : []
  if (result && tool?.server === 'retrieval' && reranked(event, receipts)) {
    resources.push('nemotron_rerank')
  }
  return {
    eventId: event.eventId,
    kind,
    sourceKind: event.eventKind,
    component,
    toolName: tool?.id ?? (toolEvent ? event.toolName : null),
    // A model call belongs to the agent's run, not a call of its own
    invocationId: event.eventKind === 'llm.call' ? event.parentInvocationId : event.invocationId,
    parentInvocationId: event.parentInvocationId,
    observedResources: resources,
  }
}

/**
 * The tool calls of a run as graph invocations. A call's component and
 * resources grow with its receipt; its state is the run projection's, so a
 * failed receipt or the end of the run settles it the same way everywhere.
 */
export const toGraphProjection = (
  run: RunProjection,
  events: readonly GraphEvent[]
): GraphProjection => {
  const invocations = run.toolCalls.map((call): GraphInvocation => {
    const own = events.filter((event) => event.invocationId === call.invocationId)
    const latest = own.findLast((event) => event.kind === 'artifact.available') ?? own.at(-1)
    return {
      invocationId: call.invocationId,
      component: latest?.component ?? (call.tool ? CALL_COMPONENT[call.tool.server].call : 'tool'),
      toolName: call.tool?.id ?? call.name,
      status: call.state,
      artifactRefs: [...call.receiptIds],
      observedResources: [...new Set(own.flatMap((event) => event.observedResources))],
    }
  })
  const status = RUN_STATUS[run.status]
  return { status, invocations, answerAvailable: status === 'completed' }
}
