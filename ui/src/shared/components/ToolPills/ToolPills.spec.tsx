// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from 'node:fs'
import path from 'node:path'
import userEvent from '@testing-library/user-event'
import { describe, expect, test } from 'vitest'
import { render, screen } from '@/test-utils'
import { PILL_ORDER, pillLabel } from './pills'
import { ToolPills } from './ToolPills'

describe('ToolPills', () => {
  test('one vocabulary with the tool registry, in its order', () => {
    const schema = JSON.parse(
      readFileSync(path.resolve(process.cwd(), '../contracts/tool-registry.schema.json'), 'utf8')
    )
    expect(PILL_ORDER).toEqual(schema.$defs.Pill.enum)
  })

  test('shows the pills in order, colored by family', () => {
    render(
      <ToolPills
        pills={[{ pill: 'ontology' }, { pill: 'kumo' }, { pill: 'duckdb' }, { pill: 'retrieval' }]}
      />
    )

    const pills = screen.getByTestId('tool-pills').querySelectorAll('.tool-pill')
    expect([...pills].map((pill) => [pill.textContent, pill.getAttribute('data-family')])).toEqual([
      ['Retrieval', 'nvidia'],
      ['DuckDB', 'partner'],
      ['Kumo', 'nvidia'],
      ['Ontology', 'nvidia'],
    ])
    expect(pillLabel({ pill: 'duckdb' })).toBe('DuckDB')
  })

  test('a pill from a run lists the tools behind it on hover', async () => {
    render(<ToolPills pills={[{ pill: 'duckdb', tools: ['query_tables'] }]} />)

    await userEvent.hover(screen.getByText('DuckDB'))
    expect((await screen.findAllByText('Table Query')).length).toBeGreaterThan(0)
  })

  test('nothing to show without pills', () => {
    render(<ToolPills pills={[]} />)
    expect(screen.queryByTestId('tool-pills')).toBeNull()
  })
})
