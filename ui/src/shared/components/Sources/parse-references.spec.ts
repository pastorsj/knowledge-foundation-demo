// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { splitReferences, tabularizeEntityLines } from './parse-references'

describe('splitReferences', () => {
  test('returns the body unchanged when there is no references block', () => {
    const content = 'The fleet has 11,463 GPUs across 12 clusters.'
    const { body, sources } = splitReferences(content)
    expect(body).toBe(content)
    expect(sources).toEqual([])
  })

  test('keeps the API evidence limitation, which precedes the Sources list, in the body', () => {
    const content =
      'A claim [1].\n\n## Evidence limitation\n\nOne or more citations did not match evidence recorded for this run.' +
      '\n\n## Sources\n\n- [1] Structured query result — top stores — evidence `hermes-receipt:abc`'
    const { body, sources } = splitReferences(content)
    expect(body).toBe(
      'A claim [1].\n\n## Evidence limitation\n\nOne or more citations did not match evidence recorded for this run.'
    )
    expect(sources).toHaveLength(1)
    expect(sources[0].evidence).toEqual({ referenceId: 'hermes-receipt:abc' })
  })

  test('strips a **References:** block and parses web sources', () => {
    const content =
      'NVIDIA shipped record volume [1].\n\n**References:**\n- [1] NVIDIA Q4 results - https://www.nvidia.com/news\n- [2] Industry brief - https://docs.example.ai/report'
    const { body, sources } = splitReferences(content)
    expect(body).toBe('NVIDIA shipped record volume [1].')
    expect(sources).toHaveLength(2)
    expect(sources[0]).toMatchObject({
      title: 'NVIDIA Q4 results',
      url: 'https://www.nvidia.com/news',
      kind: 'web',
      label: 'nvidia.com',
    })
    expect(sources[1]).toMatchObject({
      url: 'https://docs.example.ai/report',
      kind: 'web',
      label: 'docs.example.ai',
    })
  })

  test('parses the canonical `[N] Title: URL` colon form as a linked web source', () => {
    const content =
      'Revenue rose sharply [1] and [2].\n\n**References:**\n- [1] Q4 Report: https://example.com/q4\n- [2] Blog post https://blog.example.io/post'
    const { sources } = splitReferences(content)
    expect(sources).toHaveLength(2)
    expect(sources[0]).toMatchObject({
      title: 'Q4 Report',
      url: 'https://example.com/q4',
      kind: 'web',
      label: 'example.com',
    })
    expect(sources[1]).toMatchObject({
      title: 'Blog post',
      url: 'https://blog.example.io/post',
      kind: 'web',
      label: 'blog.example.io',
    })
  })

  test('parses file-with-page and bare tool references as documents', () => {
    const content =
      'See attached docs [1] and the search [2].\n\n## Sources\n- [1] fleet_overview.pdf, p.4\n- [2] knowledge_search'
    const { body, sources } = splitReferences(content)
    expect(body).toBe('See attached docs [1] and the search [2].')
    expect(sources[0]).toMatchObject({ kind: 'doc', title: 'fleet_overview.pdf, p.4' })
    expect(sources[1]).toMatchObject({
      kind: 'doc',
      title: 'knowledge_search',
      label: 'knowledge_search',
    })
  })

  test('orders sources by their [N] index regardless of line order', () => {
    const content =
      'Body.\n\n**References**\n- [2] Second - https://b.example.com\n- [1] First - https://a.example.com'
    const { sources } = splitReferences(content)
    expect(sources.map((s) => s.title)).toEqual(['First', 'Second'])
  })

  test('carries the real [N] marker on each source as its index', () => {
    const content =
      'Body.\n\n**References**\n- [1] First - https://a.example.com\n- [2] Second - https://b.example.com'
    const { sources } = splitReferences(content)
    expect(sources.map((s) => s.index)).toEqual([1, 2])
  })

  test('preserves the real index when numbering starts at [2]', () => {
    const content = 'Body [2].\n\n**References**\n- [2] Only source - https://a.example.com'
    const { sources } = splitReferences(content)
    expect(sources).toHaveLength(1)
    expect(sources[0]).toMatchObject({ index: 2, title: 'Only source' })
  })

  test('preserves the real index across a gap ([1] then [3])', () => {
    const content =
      'Body [1] and [3].\n\n**References**\n- [1] First - https://a.example.com\n- [3] Third - https://c.example.com'
    const { sources } = splitReferences(content)
    expect(sources.map((s) => s.index)).toEqual([1, 3])
    expect(sources.map((s) => s.title)).toEqual(['First', 'Third'])
  })

  test('parses run evidence with its reference and invocation ids', () => {
    const content =
      'Leaders were stable [1] and filings agree [2].\n\n**References:**\n' +
      '- [1] Structured query result — top stores — evidence `ev-1` — invocation `call_7`\n' +
      '- [2] Unstructured Retrieval evidence — 3 documents — evidence `ev-2`'
    const { sources } = splitReferences(content)
    expect(sources[0]).toMatchObject({
      label: 'Structured query result — top stores',
      kind: 'doc',
      evidence: { referenceId: 'ev-1', invocationId: 'call_7' },
    })
    expect(sources[1].evidence).toEqual({ referenceId: 'ev-2' })
  })

  test('leaves content intact when the block has no parseable lines', () => {
    const content = 'Body.\n\n**References:**\nnothing structured here'
    const { body, sources } = splitReferences(content)
    expect(body).toBe(content)
    expect(sources).toEqual([])
  })
})

describe('tabularizeEntityLines', () => {
  test('reflows 2+ consecutive label: value lines into a GFM table', () => {
    const body =
      'Top teams by job count:\n\nteam_051: ~11,285 jobs\nteam_009: ~8,210 jobs\nteam_044: ~6,140 jobs'
    const out = tabularizeEntityLines(body)
    expect(out).toContain('| Item | Value |')
    expect(out).toContain('| --- | --- |')
    expect(out).toContain('| team_051 | ~11,285 jobs |')
    expect(out).toContain('| team_044 | ~6,140 jobs |')
  })

  test('leaves prose untouched', () => {
    const body = 'The result is clear: the fleet is healthy. Utilization rose this quarter.'
    expect(tabularizeEntityLines(body)).toBe(body)
  })

  test('does not tabularize a single label: value line', () => {
    const body = 'Summary: the fleet is healthy'
    expect(tabularizeEntityLines(body)).toBe(body)
  })

  test('does not touch markdown list items', () => {
    const body = '- team_051: ~11,285 jobs\n- team_009: ~8,210 jobs'
    expect(tabularizeEntityLines(body)).toBe(body)
  })

  test('copies fenced ```chart blocks through verbatim (no table from JSON)', () => {
    const body =
      'Distribution by model:\n\n```chart\n{\n  "type": "bar",\n  "series": [{ "key": "count" }],\n  "data": [{ "model": "H100", "count": 5120 }]\n}\n```\n\nH100 leads.'
    const out = tabularizeEntityLines(body)
    expect(out).toBe(body)
    expect(out).not.toContain('| Item | Value |')
    expect(out).toContain('"count": 5120')
  })
})
