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

  test('shows the pills in order, colored by family, and names a CPU run truthfully', () => {
    render(
      <ToolPills
        pills={[
          { pill: 'retrieval' },
          { pill: 'cuml', device: 'cpu' },
          { pill: 'cudf', device: 'gpu' },
          { pill: 'kumo' },
        ]}
      />
    )

    const pills = screen.getByTestId('tool-pills').querySelectorAll('.tool-pill')
    expect([...pills].map((pill) => [pill.textContent, pill.getAttribute('data-family')])).toEqual([
      ['cuDF', 'rapids'],
      ['scikit-learn', 'rapids'],
      ['Kumo', 'nvidia'],
      ['Retrieval', 'nvidia'],
    ])
    expect(pillLabel({ pill: 'cugraph', device: 'cpu' })).toBe('NetworkX')
    expect(pillLabel({ pill: 'cugraph', device: null })).toBe('cuGraph')
  })

  test('a pill from a run lists the tools behind it on hover', async () => {
    render(
      <ToolPills
        pills={[{ pill: 'cudf', device: 'gpu', tools: ['market_scan', 'price_context'] }]}
      />
    )

    await userEvent.hover(screen.getByText('cuDF'))
    expect((await screen.findAllByText('Market Scan, Price Context')).length).toBeGreaterThan(0)
  })

  test('nothing to show without pills', () => {
    render(<ToolPills pills={[]} />)
    expect(screen.queryByTestId('tool-pills')).toBeNull()
  })
})
