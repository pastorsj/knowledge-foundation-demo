// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@/test-utils'
import { projectRun } from '../projection'
import { fixtureEvents, fixtureReceipts } from '../test-utils/fixtures'
import { ExecutionGraph, fitObservedExecutionNodes } from './ExecutionGraph'
import { toGraphEvent, toGraphProjection } from './graph-events'
import { buildExecutionGraphViewModel } from './graph-model'

const receipts = Object.fromEntries(fixtureReceipts.map((receipt) => [receipt.receiptId, receipt]))
const events = fixtureEvents.map((event) => toGraphEvent(event, receipts))
const model = buildExecutionGraphViewModel({
  allEvents: events,
  visibleEvents: events,
  projection: toGraphProjection(projectRun(fixtureEvents), events),
})
const LOGOS = new Map([
  ['retriever-tool', [{ brand: 'LangChain', src: '/ecosystem-logos/langchain.svg' }]],
  ['milvus', [{ brand: 'Milvus', src: '/ecosystem-logos/milvus.svg' }]],
])

describe('ExecutionGraph', () => {
  it('draws the legend, the groups, every node and edge, and zooms by steps', () => {
    const { container } = render(<ExecutionGraph model={model} initialZoom={0.8} />)
    const legend = screen.getByRole('list', { name: 'Execution graph legend' })
    expect(
      within(legend)
        .getAllByRole('listitem')
        .map((item) => item.textContent)
    ).toEqual(['Available now', 'Activated in this run', 'Never activated'])
    expect(container.querySelectorAll('[data-group-id]')).toHaveLength(4)
    expect(container.querySelectorAll('[data-execution-node="true"]')).toHaveLength(25)
    expect(container.querySelectorAll('[data-edge-id]')).toHaveLength(model.edges.length)

    expect(screen.getByLabelText('Execution graph zoom')).toHaveTextContent('80%')
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in execution graph' }))
    expect(screen.getByLabelText('Execution graph zoom')).toHaveTextContent('90%')
  })

  it('opens inspectable nodes the run used, and only those', () => {
    const onNodeSelect = vi.fn()
    render(<ExecutionGraph model={model} onNodeSelect={onNodeSelect} />)
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Structured Retrieval' }))
    expect(onNodeSelect).toHaveBeenCalledWith('structured-retrieval')
    fireEvent.click(screen.getByRole('button', { name: 'Inspect DuckDB Tables' }))
    expect(onNodeSelect).toHaveBeenCalledWith('structured-database')
    expect(screen.queryByRole('button', { name: 'Inspect NVIDIA Kumo' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Inspect Retrieve Evidence/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Inspect Milvus/ })).toBeNull()
  })

  it('draws the logos of the technologies behind a node, the DuckDB mark and the NVIDIA mark', () => {
    const { container } = render(
      <ExecutionGraph
        model={model}
        nodeLogos={LOGOS}
        structuredDatabaseProviderMark={{
          src: '/capability-assets/provider-duckdb.svg',
          alt: 'DuckDB',
        }}
      />
    )
    const node = (id: string) => container.querySelector(`[data-node-id="${id}"]`) as HTMLElement
    expect(within(node('retriever-tool')).getByRole('img', { name: 'LangChain' })).toHaveAttribute(
      'src',
      '/ecosystem-logos/langchain.svg'
    )
    expect(within(node('milvus')).getByRole('img', { name: 'Milvus' })).toBeInTheDocument()
    expect(within(node('structured-database')).getByRole('img', { name: 'DuckDB' })).toBeVisible()
    for (const id of ['nemotron-parse', 'nemotron-embed', 'nemotron-rerank', 'nvidia-kumo']) {
      expect(within(node(id)).getByRole('img', { name: 'NVIDIA' }), id).toBeInTheDocument()
    }
    expect(within(node('milvus')).queryByRole('img', { name: 'NVIDIA' })).toBeNull()
  })

  it('fits the observed nodes into the viewport, no larger than the initial zoom', () => {
    const fitted = fitObservedExecutionNodes({
      nodes: model.nodes,
      viewportWidth: 1000,
      viewportHeight: 600,
      maxZoom: 0.86,
    })
    expect(fitted?.zoom).toBeGreaterThanOrEqual(0.28)
    expect(fitted?.zoom).toBeLessThanOrEqual(0.86)
  })
})
