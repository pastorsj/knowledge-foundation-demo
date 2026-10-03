// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { TOOL_REGISTRY } from '@/generated/tool-registry'
import { RESOURCE_LOGOS, TOOL_LOGOS, toolFor } from './registry'
import { fixtureEvents, fixtureReceipts } from './test-utils/fixtures'

describe('tool registry', () => {
  it('finds every tool by MCP name and by Hermes name, and the names are unique', () => {
    const names = TOOL_REGISTRY.tools.flatMap((tool) => [tool.id, tool.hermes_name])
    expect(new Set(names).size).toBe(names.length)
    for (const tool of TOOL_REGISTRY.tools) {
      expect(toolFor(tool.id)).toBe(tool)
      expect(toolFor(tool.hermes_name)).toBe(tool)
    }
  })

  it('knows every tool the golden events and receipts name', () => {
    const names = [
      ...fixtureEvents.flatMap((event) => event.toolName ?? []),
      ...fixtureReceipts.map((receipt) => receipt.toolName),
    ]
    for (const name of names) expect(toolFor(name), name).toBeDefined()
  })

  it('matches receipts to their kind', () => {
    for (const receipt of fixtureReceipts) {
      expect(toolFor(receipt.toolName)?.receipt_kind).toBe(receipt.artifactKind)
    }
  })

  it('draws logos only for registered tools, from files in public/ecosystem-logos', () => {
    expect(TOOL_LOGOS.retrieve_evidence?.map((logo) => logo.brand)).toEqual(['LangChain'])
    expect(TOOL_LOGOS.query_tables?.map((logo) => logo.brand)).toEqual(['DuckDB'])
    expect(RESOURCE_LOGOS.milvus?.map((logo) => logo.brand)).toEqual(['Milvus'])
    for (const [id, logos] of [...Object.entries(TOOL_LOGOS), ...Object.entries(RESOURCE_LOGOS)]) {
      if (id in TOOL_LOGOS) expect(toolFor(id)?.id, id).toBe(id)
      for (const { src } of logos ?? []) {
        expect(src).toMatch(/^\/ecosystem-logos\/[a-z-]+\.(svg|png)$/)
        expect(existsSync(join(process.cwd(), 'public', src)), src).toBe(true)
      }
    }
  })

  it('returns undefined for an unregistered tool', () => {
    expect(toolFor('web_search')).toBeUndefined()
    expect(toolFor(null)).toBeUndefined()
  })
})
