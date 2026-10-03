// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Technology pills: which tools a demo question is expected to use, or a
 * recorded run actually used. The vocabulary is the tool registry's `Pill`
 * (contracts/tool-registry.schema.json): NVIDIA technologies in NVIDIA green,
 * DuckDB in a neutral partner tint. Hermes's own tools never get a pill.
 */

import { TOOL_REGISTRY, type Pill } from '@/generated/tool-registry'

export type { Pill }

/** One pill: its kind, and the tools that brought it. */
export interface ToolPillUse {
  pill: Pill
  tools?: string[]
}

/** Display order */
export const PILL_ORDER: readonly Pill[] = ['retrieval', 'duckdb', 'kumo', 'ontology']

export const PILLS: Readonly<Record<Pill, { label: string; family: 'nvidia' | 'partner' }>> = {
  retrieval: { label: 'Retrieval', family: 'nvidia' },
  duckdb: { label: 'DuckDB', family: 'partner' },
  kumo: { label: 'Kumo', family: 'nvidia' },
  ontology: { label: 'Ontology', family: 'nvidia' },
}

const TOOLS = new Map(TOOL_REGISTRY.tools.map((tool) => [tool.id, tool]))

export const isPill = (value: unknown): value is Pill =>
  typeof value === 'string' && (PILL_ORDER as readonly string[]).includes(value)

/** A pill's label. */
export const pillLabel = ({ pill }: ToolPillUse): string => PILLS[pill].label

/** The display names of the tools behind a pill, for its tooltip. */
export const pillToolLabels = ({ tools = [] }: ToolPillUse): string[] =>
  tools.map((id) => TOOLS.get(id)?.label ?? id)

/**
 * The pills as words, for assistive technology: "DuckDB (Table Query), Retrieval (Document Retrieval)".
 * A pill's tooltip opens on hover only, and a recorded session's button names the session alone.
 */
export const describePills = (pills: readonly ToolPillUse[]): string =>
  orderPills(pills)
    .map((use) => {
      const tools = pillToolLabels(use)
      return tools.length ? `${pillLabel(use)} (${tools.join(', ')})` : pillLabel(use)
    })
    .join(', ')

/** Pills in display order. */
export const orderPills = (pills: readonly ToolPillUse[]): ToolPillUse[] =>
  [...pills].sort((a, b) => PILL_ORDER.indexOf(a.pill) - PILL_ORDER.indexOf(b.pill))

/**
 * The pills of a recorded session's turns, as `demo-api record` computes them
 * (api/src/demo_api/pills.py): each completed registered tool call (an
 * `artifact.available` event) brings its tool's pills. For bundles recorded
 * before the index carried them.
 */
export const sessionPills = (turns: ReadonlyArray<{ events?: unknown[] }>): ToolPillUse[] => {
  const found = new Map<Pill, ToolPillUse & { tools: string[] }>()
  for (const turn of turns) {
    for (const event of turn.events ?? []) {
      if (!isObject(event) || event.eventKind !== 'artifact.available') continue
      const tool = TOOLS.get(String(event.toolName))
      if (!tool) continue
      for (const pill of tool.pills) {
        const use = found.get(pill) ?? { pill, tools: [] }
        if (!use.tools.includes(tool.id)) use.tools.push(tool.id)
        found.set(pill, use)
      }
    }
  }
  return orderPills([...found.values()])
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)
