// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The execution graph: one fixed topology of everything a Hermes run can use
 * (tool control, structured data, documents, synthesis). Events only change
 * node and edge states, so the layout never moves during replay and
 * capabilities a run did not use stay visible as such.
 */

import type {
  GraphEvent,
  GraphInvocation,
  GraphInvocationStatus,
  GraphProjection,
  GraphResource,
} from './graph-events'

export type ExecutionNodeState = 'unobserved' | 'pending' | 'running' | 'completed' | 'failed'

export const isInspectableExecutionNodeState = (state: ExecutionNodeState): boolean =>
  state === 'running' || state === 'completed' || state === 'failed'

export type ExecutionNodeId =
  | 'business-question'
  | 'hermes-agent'
  | 'tool-search'
  | 'tool-describe'
  | 'tool-call'
  | 'skills-list'
  | 'skill-view'
  | 'ontology-tool'
  | 'tables-tool'
  | 'prediction-tool'
  | 'retriever-tool'
  | 'hermes-tools'
  | 'structured-prediction'
  | 'nvidia-kumo'
  | 'nvidia-ontology'
  | 'structured-database'
  | 'structured-retrieval'
  | 'nemotron-parse'
  | 'nemotron-embed'
  | 'milvus'
  | 'nemotron-rerank'
  | 'unstructured-retrieval'
  | 'synthesis'
  | 'report-generation'
  | 'trusted-answer'

export type InspectableExecutionNodeId =
  | 'hermes-agent'
  | 'nvidia-kumo'
  | 'nvidia-ontology'
  | 'structured-retrieval'
  | 'unstructured-retrieval'
  | 'structured-database'

export type NodeIcon =
  | 'question'
  | 'router'
  | 'model'
  | 'agent'
  | 'tools'
  | 'prediction'
  | 'ontology'
  | 'database'
  | 'table'
  | 'retrieval'
  | 'parse'
  | 'embed'
  | 'rerank'
  | 'synthesis'
  | 'report'
  | 'answer'

type Port = 'top' | 'right' | 'bottom' | 'left'

export type ExecutionGraphGroupId =
  | 'tool-control'
  | 'agent-utilities'
  | 'structured-data'
  | 'unstructured-data'

export type ExecutionGraphNodeKind = 'input' | 'agent' | 'tool' | 'stage' | 'resource' | 'output'

export type ExecutionGraphGroupDefinition = {
  id: ExecutionGraphGroupId
  label: string
  x: number
  y: number
  width: number
  height: number
  description?: string
}

export type ExecutionGraphNodeDefinition = {
  id: ExecutionNodeId
  label: string
  subtitle: string
  x: number
  y: number
  width?: number
  height?: number
  icon: NodeIcon
  branch: 'orchestration' | 'prediction' | 'structured' | 'unstructured' | 'foundation'
  group?: ExecutionGraphGroupId
  kind?: ExecutionGraphNodeKind
  inspectable?: boolean
}

export type ExecutionGraphEdgeDefinition = {
  id: string
  from: ExecutionNodeId
  to: ExecutionNodeId
  label?: string
  fromPort: Port
  toPort: Port
  fromOffset?: number
  toOffset?: number
  via?: ReadonlyArray<readonly [number, number]>
  labelAt?: readonly [number, number]
}

export type ExecutionNodeView = ExecutionGraphNodeDefinition & {
  state: ExecutionNodeState
  count: number
  current: boolean
}

export type ExecutionEdgeView = ExecutionGraphEdgeDefinition & {
  state: ExecutionNodeState
  current: boolean
}

export type ExecutionGraphViewModel = {
  nodes: ExecutionNodeView[]
  edges: ExecutionEdgeView[]
  groups: ExecutionGraphGroupDefinition[]
  canvasWidth: number
  canvasHeight: number
}

export type ExecutionNodeDetail = {
  id: InspectableExecutionNodeId
  label: string
  subtitle: string
  state: ExecutionNodeState
  observed: boolean
  invocations: Array<{
    invocationId: string
    name: string
    status: GraphInvocationStatus
    artifactRefs: string[]
  }>
  artifactRefs: string[]
}

export const EXECUTION_NODE_WIDTH = 180
export const EXECUTION_NODE_HEIGHT = 80

/**
 * The topology is fixed at run start; events only change node and edge state.
 * Direct Hermes tools are drawn apart from the stages and resources behind them.
 * Structured data has three rows (Auto Ontology, DuckDB tables, NVIDIA Kumo);
 * documents are one chain, from the parsed corpus to the selected evidence.
 */
export const hermesExecutionGraphNodes: readonly ExecutionGraphNodeDefinition[] = [
  {
    id: 'business-question',
    label: 'Business Question',
    subtitle: 'Requested outcome and context',
    x: 40,
    y: 510,
    icon: 'question',
    branch: 'orchestration',
    kind: 'input',
  },
  {
    id: 'hermes-agent',
    label: 'Hermes Agent',
    subtitle: 'Plans, invokes tools, and evaluates results',
    x: 350,
    y: 510,
    width: 220,
    icon: 'agent',
    branch: 'orchestration',
    kind: 'agent',
    inspectable: true,
  },
  {
    id: 'tool-search',
    label: 'Tool Search',
    subtitle: 'Discovers available tool definitions',
    x: 750,
    y: 120,
    icon: 'tools',
    branch: 'orchestration',
    group: 'tool-control',
    kind: 'tool',
  },
  {
    id: 'tool-describe',
    label: 'Tool Describe',
    subtitle: 'Loads a selected tool schema',
    x: 1030,
    y: 120,
    icon: 'tools',
    branch: 'orchestration',
    group: 'tool-control',
    kind: 'tool',
  },
  {
    id: 'tool-call',
    label: 'Tool Invocation',
    subtitle: 'Invokes a selected or directly exposed tool',
    x: 1310,
    y: 120,
    icon: 'tools',
    branch: 'orchestration',
    group: 'tool-control',
    kind: 'tool',
  },
  {
    id: 'hermes-tools',
    label: 'Other Tools',
    subtitle: 'Observed tools outside this manifest',
    x: 1590,
    y: 120,
    width: 220,
    icon: 'tools',
    branch: 'orchestration',
    group: 'tool-control',
    kind: 'tool',
  },
  {
    id: 'skills-list',
    label: 'Skills List',
    subtitle: 'Discovers reviewed agent guidance',
    x: 750,
    y: 1020,
    icon: 'tools',
    branch: 'orchestration',
    group: 'agent-utilities',
    kind: 'tool',
  },
  {
    id: 'skill-view',
    label: 'Skill View',
    subtitle: 'Loads selected skill instructions',
    x: 1030,
    y: 1020,
    icon: 'tools',
    branch: 'orchestration',
    group: 'agent-utilities',
    kind: 'tool',
  },
  {
    id: 'ontology-tool',
    label: 'Ask Ontology',
    subtitle: 'ask_question · MCP tool',
    x: 750,
    y: 420,
    icon: 'tools',
    branch: 'structured',
    group: 'structured-data',
    kind: 'tool',
  },
  {
    id: 'nvidia-ontology',
    label: 'Auto Ontology',
    subtitle: 'Semantic grounding resource',
    x: 1050,
    y: 420,
    icon: 'ontology',
    branch: 'foundation',
    group: 'structured-data',
    kind: 'resource',
    inspectable: true,
  },
  {
    id: 'tables-tool',
    label: 'Query Tables',
    subtitle: 'query_tables · MCP tool',
    x: 750,
    y: 570,
    icon: 'tools',
    branch: 'structured',
    group: 'structured-data',
    kind: 'tool',
  },
  {
    id: 'structured-retrieval',
    label: 'Structured Retrieval',
    subtitle: 'Read-only SQL and its rows',
    x: 1370,
    y: 570,
    icon: 'table',
    branch: 'structured',
    group: 'structured-data',
    kind: 'stage',
    inspectable: true,
  },
  {
    id: 'structured-database',
    label: 'DuckDB Tables',
    subtitle: 'Selected read-only sources',
    x: 1690,
    y: 570,
    icon: 'database',
    branch: 'foundation',
    group: 'structured-data',
    kind: 'resource',
    inspectable: true,
  },
  {
    id: 'prediction-tool',
    label: 'Predict',
    subtitle: 'predict · MCP tool',
    x: 750,
    y: 720,
    icon: 'tools',
    branch: 'prediction',
    group: 'structured-data',
    kind: 'tool',
  },
  {
    id: 'structured-prediction',
    label: 'Structured Prediction',
    subtitle: 'PQL scored per entity',
    x: 1370,
    y: 720,
    icon: 'prediction',
    branch: 'prediction',
    group: 'structured-data',
    kind: 'stage',
  },
  {
    id: 'nvidia-kumo',
    label: 'NVIDIA Kumo',
    subtitle: 'Relational foundation model',
    x: 1690,
    y: 720,
    icon: 'model',
    branch: 'foundation',
    group: 'structured-data',
    kind: 'resource',
    inspectable: true,
  },
  {
    id: 'retriever-tool',
    label: 'Retrieve Evidence',
    subtitle: 'retrieve_evidence · MCP tool',
    x: 1420,
    y: 1020,
    icon: 'tools',
    branch: 'unstructured',
    group: 'unstructured-data',
    kind: 'tool',
  },
  {
    id: 'nemotron-parse',
    label: 'Nemotron Parse',
    subtitle: 'Parsed the documents at ingest',
    x: 1720,
    y: 1020,
    icon: 'parse',
    branch: 'foundation',
    group: 'unstructured-data',
    kind: 'resource',
  },
  {
    id: 'nemotron-embed',
    label: 'Nemotron Embed',
    subtitle: 'Passage and query vectors',
    x: 2020,
    y: 1020,
    icon: 'embed',
    branch: 'foundation',
    group: 'unstructured-data',
    kind: 'resource',
  },
  {
    id: 'milvus',
    label: 'Milvus',
    subtitle: 'Vector search per source',
    x: 2320,
    y: 1020,
    icon: 'database',
    branch: 'foundation',
    group: 'unstructured-data',
    kind: 'resource',
  },
  {
    id: 'nemotron-rerank',
    label: 'Nemotron Rerank',
    subtitle: 'Reorders the candidates',
    x: 2620,
    y: 1020,
    icon: 'rerank',
    branch: 'foundation',
    group: 'unstructured-data',
    kind: 'resource',
  },
  {
    id: 'unstructured-retrieval',
    label: 'Unstructured Retrieval',
    subtitle: 'Cited passages · evidence',
    x: 2920,
    y: 1020,
    width: 220,
    icon: 'retrieval',
    branch: 'unstructured',
    group: 'unstructured-data',
    kind: 'stage',
    inspectable: true,
  },
  {
    id: 'synthesis',
    label: 'Synthesis',
    subtitle: 'Combines observed results and evidence',
    x: 2120,
    y: 560,
    width: 220,
    icon: 'synthesis',
    branch: 'orchestration',
    kind: 'stage',
  },
  {
    id: 'report-generation',
    label: 'Response Formatting',
    subtitle: 'Readable Markdown and citations',
    x: 2460,
    y: 560,
    width: 210,
    icon: 'report',
    branch: 'orchestration',
    kind: 'stage',
  },
  {
    id: 'trusted-answer',
    label: 'Answer',
    subtitle: 'Inspectable final response',
    x: 2800,
    y: 560,
    icon: 'answer',
    branch: 'orchestration',
    kind: 'output',
  },
]

export const hermesExecutionGraphGroups: readonly ExecutionGraphGroupDefinition[] = [
  {
    id: 'tool-control',
    label: 'Tool Control',
    description: 'Runtime discovery and invocation tools',
    x: 700,
    y: 55,
    width: 1140,
    height: 190,
  },
  {
    id: 'structured-data',
    label: 'Structured Data',
    description: 'DuckDB tables · Auto Ontology · NVIDIA Kumo predictions',
    x: 700,
    y: 350,
    width: 1200,
    height: 485,
  },
  {
    id: 'agent-utilities',
    label: 'Agent Utilities',
    description: 'Reviewed guidance available to Hermes',
    x: 700,
    y: 955,
    width: 590,
    height: 190,
  },
  {
    id: 'unstructured-data',
    label: 'Documents',
    description: 'Nemotron Parse · Nemotron Embed · Milvus · Nemotron Rerank',
    x: 1370,
    y: 955,
    width: 1820,
    height: 205,
  },
]

export const HERMES_EXECUTION_CANVAS_WIDTH = 3260
export const HERMES_EXECUTION_CANVAS_HEIGHT = 1200

/** The tool calls' shared dispatch bus, left of the structured rows */
const DISPATCH_BUS: ReadonlyArray<readonly [number, number]> = [
  [1400, 300],
  [620, 300],
]

export const hermesExecutionGraphEdges: readonly ExecutionGraphEdgeDefinition[] = [
  {
    id: 'question-hermes',
    from: 'business-question',
    to: 'hermes-agent',
    label: 'REQUEST',
    fromPort: 'right',
    toPort: 'left',
    labelAt: [285, 542],
  },
  {
    id: 'hermes-tool-search',
    from: 'hermes-agent',
    to: 'tool-search',
    label: 'DISCOVERS TOOLS',
    fromPort: 'right',
    fromOffset: -20,
    toPort: 'bottom',
    via: [
      [600, 530],
      [600, 280],
      [840, 280],
    ],
    labelAt: [720, 272],
  },
  {
    id: 'tool-search-describe',
    from: 'tool-search',
    to: 'tool-describe',
    label: 'SELECTS',
    fromPort: 'right',
    toPort: 'left',
    labelAt: [980, 152],
  },
  {
    id: 'tool-describe-call',
    from: 'tool-describe',
    to: 'tool-call',
    label: 'SCHEMA',
    fromPort: 'right',
    toPort: 'left',
    labelAt: [1260, 152],
  },
  {
    id: 'hermes-tools',
    from: 'hermes-agent',
    to: 'hermes-tools',
    label: 'OTHER TOOL',
    fromPort: 'top',
    fromOffset: 30,
    toPort: 'right',
    via: [
      [490, 40],
      [1880, 40],
      [1880, 160],
    ],
    labelAt: [1185, 32],
  },
  {
    id: 'hermes-skills',
    from: 'hermes-agent',
    to: 'skills-list',
    label: 'DISCOVERS GUIDANCE',
    fromPort: 'bottom',
    fromOffset: -30,
    toPort: 'top',
    via: [
      [430, 910],
      [840, 910],
    ],
    labelAt: [635, 902],
  },
  {
    id: 'skills-list-view',
    from: 'skills-list',
    to: 'skill-view',
    label: 'OPENS',
    fromPort: 'right',
    toPort: 'left',
    labelAt: [980, 1052],
  },
  {
    id: 'tool-call-ontology',
    from: 'tool-call',
    to: 'ontology-tool',
    label: 'DISPATCHES',
    fromPort: 'bottom',
    toPort: 'left',
    via: [...DISPATCH_BUS, [620, 460]],
    labelAt: [685, 452],
  },
  {
    id: 'tool-call-tables',
    from: 'tool-call',
    to: 'tables-tool',
    fromPort: 'bottom',
    toPort: 'left',
    via: [...DISPATCH_BUS, [620, 610]],
  },
  {
    id: 'tool-call-prediction',
    from: 'tool-call',
    to: 'prediction-tool',
    fromPort: 'bottom',
    toPort: 'left',
    via: [...DISPATCH_BUS, [620, 760]],
  },
  {
    id: 'ontology-tool-resource',
    from: 'ontology-tool',
    to: 'nvidia-ontology',
    label: 'USES',
    fromPort: 'right',
    toPort: 'left',
    labelAt: [990, 452],
  },
  {
    id: 'ontology-query',
    from: 'nvidia-ontology',
    to: 'structured-retrieval',
    label: 'GROUNDS SQL',
    fromPort: 'right',
    toPort: 'top',
    toOffset: -30,
    via: [[1430, 460]],
  },
  {
    id: 'tables-query',
    from: 'tables-tool',
    to: 'structured-retrieval',
    label: 'RUNS SQL',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'query-source',
    from: 'structured-retrieval',
    to: 'structured-database',
    label: 'READS',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'prediction-query',
    from: 'prediction-tool',
    to: 'structured-prediction',
    label: 'PQL',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'predict-kumo',
    from: 'structured-prediction',
    to: 'nvidia-kumo',
    label: 'INFERENCE',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'source-kumo',
    from: 'structured-database',
    to: 'nvidia-kumo',
    label: 'GRAPH',
    fromPort: 'bottom',
    toPort: 'top',
    labelAt: [1840, 685],
  },
  {
    id: 'tool-call-retriever',
    from: 'tool-call',
    to: 'retriever-tool',
    label: 'DISPATCHES',
    fromPort: 'bottom',
    toPort: 'top',
    via: [...DISPATCH_BUS, [620, 900], [1510, 900]],
    labelAt: [1065, 892],
  },
  {
    id: 'retriever-parse',
    from: 'retriever-tool',
    to: 'nemotron-parse',
    label: 'CORPUS',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'parse-embed',
    from: 'nemotron-parse',
    to: 'nemotron-embed',
    label: 'CHUNKS',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'embed-milvus',
    from: 'nemotron-embed',
    to: 'milvus',
    label: 'VECTORS',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'milvus-rerank',
    from: 'milvus',
    to: 'nemotron-rerank',
    label: 'HITS',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'rerank-evidence',
    from: 'nemotron-rerank',
    to: 'unstructured-retrieval',
    label: 'RANKED',
    fromPort: 'right',
    toPort: 'left',
  },
  // A retrieval without a rerank model keeps the vector order (BYPASS_EDGE)
  {
    id: 'milvus-evidence',
    from: 'milvus',
    to: 'unstructured-retrieval',
    label: 'VECTOR ORDER',
    fromPort: 'bottom',
    toPort: 'bottom',
    via: [
      [2410, 1135],
      [3030, 1135],
    ],
  },
  {
    id: 'query-synthesis',
    from: 'structured-retrieval',
    to: 'synthesis',
    label: 'SQL ROWS',
    fromPort: 'top',
    fromOffset: 60,
    toPort: 'top',
    toOffset: -40,
    via: [
      [1520, 330],
      [2190, 330],
    ],
  },
  {
    id: 'predict-synthesis',
    from: 'nvidia-kumo',
    to: 'synthesis',
    label: 'PREDICTION RESULT',
    fromPort: 'right',
    toPort: 'left',
    toOffset: 10,
  },
  {
    id: 'retrieve-synthesis',
    from: 'unstructured-retrieval',
    to: 'synthesis',
    label: 'SELECTED EVIDENCE',
    fromPort: 'top',
    fromOffset: 0,
    toPort: 'bottom',
    toOffset: 20,
    via: [
      [3030, 900],
      [2250, 900],
    ],
  },
  {
    id: 'hermes-synthesis',
    from: 'hermes-agent',
    to: 'synthesis',
    label: 'SYNTHESIZES',
    fromPort: 'top',
    toPort: 'top',
    toOffset: 60,
    via: [
      [460, 16],
      [2290, 16],
    ],
    labelAt: [1400, 8],
  },
  {
    id: 'synthesis-report',
    from: 'synthesis',
    to: 'report-generation',
    label: 'COMPILES',
    fromPort: 'right',
    toPort: 'left',
  },
  {
    id: 'report-answer',
    from: 'report-generation',
    to: 'trusted-answer',
    label: 'PUBLISHES',
    fromPort: 'right',
    toPort: 'left',
  },
]

/** The edge a retrieval without a rerank model takes instead of Nemotron Rerank. */
const BYPASS_EDGE = 'milvus-evidence'

const hermesComponentNodeMap: Partial<Record<GraphEvent['component'], ExecutionNodeId[]>> = {
  agent: ['hermes-agent'],
  ontology: ['nvidia-ontology'],
  structured_retrieval: ['structured-retrieval'],
  structured_prediction: ['structured-prediction'],
  unstructured_retrieval: ['unstructured-retrieval'],
}

/**
 * The node of each tool drawn as its own node, by registered tool id (and the
 * Hermes utility tools by name). Any other tool is drawn as Other Tools.
 */
export const TOOL_NODE_BY_NAME: Readonly<Partial<Record<string, ExecutionNodeId>>> = {
  tool_search: 'tool-search',
  tool_describe: 'tool-describe',
  tool_call: 'tool-call',
  skills_list: 'skills-list',
  skill_view: 'skill-view',
  ask_question: 'ontology-tool',
  query_tables: 'tables-tool',
  predict: 'prediction-tool',
  retrieve_evidence: 'retriever-tool',
}

/** Tools the agent dispatches through Tool Invocation. */
const DISPATCHED_TOOL_NODES: ReadonlySet<ExecutionNodeId> = new Set([
  'ontology-tool',
  'tables-tool',
  'prediction-tool',
  'retriever-tool',
])

/** The node of each service a call is known to have used. */
const RESOURCE_NODES: Readonly<Record<GraphResource, ExecutionNodeId>> = {
  structured_database: 'structured-database',
  nvidia_kumo: 'nvidia-kumo',
  nvidia_ontology: 'nvidia-ontology',
  nemotron_parse: 'nemotron-parse',
  nemotron_embed: 'nemotron-embed',
  milvus: 'milvus',
  nemotron_rerank: 'nemotron-rerank',
}

/**
 * A registered capability tool without a node of its own is drawn by its
 * capability (component and resources).
 */
const toolNode = (event: GraphEvent): ExecutionNodeId | undefined => {
  if (!event.toolName) return undefined
  const node = TOOL_NODE_BY_NAME[event.toolName]
  if (node) return node
  // A tool with no node of its own and no capability node, e.g. a Hermes
  // utility or a newer registered tool, is drawn as Other Tools.
  return hermesComponentNodeMap[event.component] ? undefined : 'hermes-tools'
}

/**
 * Resources light only from the resources a call is known to have used; a
 * display name is never enough.
 */
const resourceMatches = (event: GraphEvent): ExecutionNodeId[] =>
  event.observedResources.map((resource) => RESOURCE_NODES[resource])

export const nodeIdsForEvent = (event: GraphEvent): ExecutionNodeId[] => {
  const nodeIds = new Set<ExecutionNodeId>(hermesComponentNodeMap[event.component] || [])
  const directToolNode = toolNode(event)
  if (directToolNode) {
    nodeIds.add(directToolNode)
    // Hermes calls a configured MCP tool directly, without a separate
    // `tool_call` event; the call still proves the Tool Invocation stage ran.
    if (DISPATCHED_TOOL_NODES.has(directToolNode)) nodeIds.add('tool-call')
  }
  resourceMatches(event).forEach((nodeId) => nodeIds.add(nodeId))

  if (event.kind === 'run.started') {
    nodeIds.add('business-question')
    nodeIds.add('hermes-agent')
  }
  if (event.kind === 'run.completed') {
    nodeIds.add('report-generation')
    nodeIds.add('synthesis')
    nodeIds.add('trusted-answer')
  }
  if (event.sourceKind === 'reasoning.available') nodeIds.add('synthesis')

  return [...nodeIds]
}

const eventState = (event: GraphEvent): Exclude<ExecutionNodeState, 'pending' | 'unobserved'> => {
  if (event.kind.endsWith('.failed')) return 'failed'
  if (event.kind.endsWith('.started')) return 'running'
  return 'completed'
}

const matchingEvents = (nodeId: ExecutionNodeId, events: readonly GraphEvent[]): GraphEvent[] =>
  events.filter((event) => nodeIdsForEvent(event).includes(nodeId))

const uniqueInvocationCount = (events: readonly GraphEvent[]): number => {
  const invocationIds = new Set(
    events.flatMap((event) => (event.invocationId ? [event.invocationId] : []))
  )
  return invocationIds.size
}

/** An invocation as the event that settled it, to find its nodes. */
const invocationEvent = (invocation: GraphInvocation): GraphEvent => ({
  eventId: invocation.invocationId,
  kind:
    invocation.status === 'running'
      ? 'invocation.started'
      : invocation.status === 'failed'
        ? 'invocation.failed'
        : 'invocation.completed',
  sourceKind: 'tool.completed',
  component: invocation.component,
  toolName: invocation.toolName,
  invocationId: invocation.invocationId,
  parentInvocationId: null,
  observedResources: invocation.observedResources,
})

const invocationMatchesNode = (nodeId: ExecutionNodeId, invocation: GraphInvocation): boolean =>
  nodeIdsForEvent(invocationEvent(invocation)).includes(nodeId)

const nodeState = (
  nodeId: ExecutionNodeId,
  visibleEvents: readonly GraphEvent[],
  allEvents: readonly GraphEvent[],
  projection: GraphProjection
): ExecutionNodeState => {
  const visible = matchingEvents(nodeId, visibleEvents)
  const relatedInvocations = projection.invocations.filter((invocation) =>
    invocationMatchesNode(nodeId, invocation)
  )

  // A capability can be retried after a failed attempt. While replay is in
  // progress, summarize the latest observed attempt so the graph reflects the
  // action at the current cursor. Once the run is terminal, a completed attempt
  // proves that the capability succeeded even if a later optional retry failed.
  // Every attempt remains available in the node detail and timeline.
  if (relatedInvocations.length) {
    if (projection.status === 'completed' || projection.status === 'failed') {
      if (relatedInvocations.some((invocation) => invocation.status === 'completed')) {
        return 'completed'
      }
      if (relatedInvocations.some((invocation) => invocation.status === 'failed')) return 'failed'
    }
    const latestInvocationId = [...visible]
      .reverse()
      .find((event) => event.invocationId)?.invocationId
    const latestInvocation = relatedInvocations.find(
      (invocation) => invocation.invocationId === latestInvocationId
    )
    if (latestInvocation) return latestInvocation.status
  }

  if (relatedInvocations.some((invocation) => invocation.status === 'failed')) return 'failed'
  if (relatedInvocations.some((invocation) => invocation.status === 'running')) return 'running'
  if (relatedInvocations.length) return 'completed'
  // The question has no completion event of its own. Once the run ends it has
  // necessarily been handed to the agent.
  if (
    nodeId === 'business-question' &&
    projection.status !== 'idle' &&
    projection.status !== 'running'
  ) {
    return 'completed'
  }
  if (!visible.length) {
    if (nodeId === 'business-question' && projection.status !== 'idle') return 'completed'
    if (nodeId === 'trusted-answer' && projection.answerAvailable) return 'completed'
    if (nodeId === 'report-generation' && projection.status === 'completed') return 'completed'
    if (nodeId === 'synthesis' && projection.answerAvailable) return 'completed'
    return matchingEvents(nodeId, allEvents).length ? 'pending' : 'unobserved'
  }
  return eventState(visible[visible.length - 1])
}

const edgeState = (from: ExecutionNodeState, to: ExecutionNodeState): ExecutionNodeState => {
  if (from === 'unobserved' || to === 'unobserved') return 'unobserved'
  if (from === 'pending' || to === 'pending') return 'pending'
  if (from === 'failed' || to === 'failed') return 'failed'
  if (from === 'running' || to === 'running') return 'running'
  return 'completed'
}

/**
 * The events that prove an edge: one event that lights both ends, or both
 * ends observed in an order that the edge implies.
 */
const edgeEvidence = (
  edge: ExecutionGraphEdgeDefinition,
  events: readonly GraphEvent[]
): Set<string> | null => {
  const eventIds = new Set<string>()
  const fromEvents = matchingEvents(edge.from, events)
  const toEvents = matchingEvents(edge.to, events)

  // A single typed event may prove both a capability and its observed resource,
  // or a terminal transition and the output stages it completes.
  events.forEach((event) => {
    const nodes = nodeIdsForEvent(event)
    if (nodes.includes(edge.from) && nodes.includes(edge.to)) eventIds.add(event.eventId)
  })

  const from = fromEvents[0]
  const to = toEvents[0]
  if (!from || !to) return eventIds.size ? eventIds : null

  const fromIndex = events.indexOf(from)
  const toIndex = events.indexOf(to)
  const agentOwnsTarget = edge.from === 'hermes-agent' && edge.to !== 'synthesis'
  const returnsToSynthesis = edge.to === 'synthesis' && edge.from !== 'hermes-agent'
  const isLinearOutputEdge =
    edge.id === 'hermes-synthesis' || edge.id === 'synthesis-report' || edge.id === 'report-answer'
  const isOrderedToolEdge =
    edge.id === 'tool-search-describe' ||
    edge.id === 'tool-describe-call' ||
    edge.id === 'tool-call-ontology' ||
    edge.id === 'tool-call-tables' ||
    edge.id === 'tool-call-prediction' ||
    edge.id === 'tool-call-retriever' ||
    edge.id === 'skills-list-view'

  if (
    edge.id === 'question-hermes' ||
    agentOwnsTarget ||
    (returnsToSynthesis && fromIndex <= toIndex) ||
    (isOrderedToolEdge && fromIndex <= toIndex) ||
    (isLinearOutputEdge && fromIndex <= toIndex)
  ) {
    eventIds.add(from.eventId)
    eventIds.add(to.eventId)
  }

  return eventIds.size ? eventIds : null
}

export const buildExecutionGraphViewModel = ({
  allEvents,
  visibleEvents,
  projection,
}: {
  allEvents: readonly GraphEvent[]
  visibleEvents: readonly GraphEvent[]
  projection: GraphProjection
}): ExecutionGraphViewModel => {
  const currentEvent = visibleEvents[visibleEvents.length - 1]
  const currentNodeIds = new Set(currentEvent ? nodeIdsForEvent(currentEvent) : [])
  const states = new Map<ExecutionNodeId, ExecutionNodeState>()

  const nodes = hermesExecutionGraphNodes.map((node) => {
    const state = nodeState(node.id, visibleEvents, allEvents, projection)
    states.set(node.id, state)
    return {
      ...node,
      state,
      current: currentNodeIds.has(node.id),
      count: uniqueInvocationCount(matchingEvents(node.id, visibleEvents)),
    }
  })

  // A run whose retrieval reranked never takes the bypass, even before the rerank is reached
  const reranked = allEvents.some((event) => nodeIdsForEvent(event).includes('nemotron-rerank'))
  const edges = hermesExecutionGraphEdges.map((edge) => {
    if (edge.id === BYPASS_EDGE && reranked) {
      return { ...edge, state: 'unobserved' as const, current: false }
    }
    const visibleEvidence = edgeEvidence(edge, visibleEvents)
    const futureEvidence = visibleEvidence ? null : edgeEvidence(edge, allEvents)
    const state = visibleEvidence
      ? edgeState(states.get(edge.from) || 'unobserved', states.get(edge.to) || 'unobserved')
      : futureEvidence
        ? 'pending'
        : 'unobserved'
    return {
      ...edge,
      state,
      current: Boolean(currentEvent && visibleEvidence?.has(currentEvent.eventId)),
    }
  })

  return {
    nodes,
    edges,
    groups: [...hermesExecutionGraphGroups],
    canvasWidth: HERMES_EXECUTION_CANVAS_WIDTH,
    canvasHeight: HERMES_EXECUTION_CANVAS_HEIGHT,
  }
}

export const buildExecutionNodeDetail = (
  nodeId: InspectableExecutionNodeId,
  graph: ExecutionGraphViewModel,
  projection: GraphProjection
): ExecutionNodeDetail => {
  const node = graph.nodes.find((candidate) => candidate.id === nodeId)
  if (!node) throw new Error(`Unknown execution node: ${nodeId}`)

  const invocations = projection.invocations
    .filter((invocation) => invocationMatchesNode(nodeId, invocation))
    .map((invocation) => ({
      invocationId: invocation.invocationId,
      name: invocation.toolName || 'Observed invocation',
      status: invocation.status,
      artifactRefs: invocation.artifactRefs,
    }))
  const artifactRefs = [...new Set(invocations.flatMap((invocation) => invocation.artifactRefs))]

  return {
    id: nodeId,
    label: node.label,
    subtitle: node.subtitle,
    state: node.state,
    observed: node.state !== 'unobserved' && node.state !== 'pending',
    invocations,
    artifactRefs,
  }
}

const INSPECTABLE_NODE_IDS: ReadonlySet<ExecutionNodeId> = new Set(
  hermesExecutionGraphNodes.filter((node) => node.inspectable).map((node) => node.id)
)

export const isInspectableNode = (nodeId: ExecutionNodeId): nodeId is InspectableExecutionNodeId =>
  INSPECTABLE_NODE_IDS.has(nodeId)
