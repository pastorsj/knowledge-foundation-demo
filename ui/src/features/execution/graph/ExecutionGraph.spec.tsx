// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@/test-utils'
import { projectRun } from '../projection'
import { fixtureEvents, receiptOf } from '../test-utils/fixtures'
import { ExecutionGraph, fitObservedExecutionNodes } from './ExecutionGraph'
import { buildGpuAccelerationByNode } from './gpu-acceleration'
import { toGraphEvent, toGraphProjection } from './graph-events'
import { buildExecutionGraphViewModel } from './graph-model'

const events = fixtureEvents.map(toGraphEvent)
const model = buildExecutionGraphViewModel({
  allEvents: events,
  visibleEvents: events,
  projection: toGraphProjection(projectRun(fixtureEvents), events),
})
const LOGOS = new Map([
  ['retriever-tool', [{ brand: 'LangChain', src: '/ecosystem-logos/langchain.svg' }]],
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
    expect(container.querySelectorAll('[data-group-id]')).toHaveLength(5)
    expect(container.querySelectorAll('[data-execution-node="true"]')).toHaveLength(25)
    expect(container.querySelectorAll('[data-edge-id]')).toHaveLength(model.edges.length)

    expect(screen.getByLabelText('Execution graph zoom')).toHaveTextContent('80%')
    fireEvent.click(screen.getByRole('button', { name: 'Zoom in execution graph' }))
    expect(screen.getByLabelText('Execution graph zoom')).toHaveTextContent('90%')
  })

  it('opens inspectable nodes the run used, and only those', () => {
    const onNodeSelect = vi.fn()
    render(<ExecutionGraph model={model} onNodeSelect={onNodeSelect} />)
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Market Anomaly Scan' }))
    expect(onNodeSelect).toHaveBeenCalledWith('market-anomaly-scan')
    expect(screen.queryByRole('button', { name: 'Inspect Market Scan' })).toBeNull()
    expect(screen.queryByRole('button', { name: /Inspect Retrieve Evidence/ })).toBeNull()
  })

  it('draws the LangChain logo on Retrieve Evidence and the GPU badge from receipts', () => {
    const { container } = render(
      <ExecutionGraph
        model={model}
        nodeLogos={LOGOS}
        gpuAccelerations={buildGpuAccelerationByNode([receiptOf('analytics_result')])}
      />
    )
    const retrieval = container.querySelector('[data-node-id="retriever-tool"]') as HTMLElement
    expect(within(retrieval).getByRole('img', { name: 'LangChain' })).toHaveAttribute(
      'src',
      '/ecosystem-logos/langchain.svg'
    )
    expect(screen.getAllByTestId('gpu-acceleration-badge')).toHaveLength(1)
    expect(screen.getByTestId('gpu-legend-badge')).toBeInTheDocument()
    expect(container.querySelector('[data-node-id="market-anomaly-scan"]')).toHaveAttribute(
      'data-gpu-accelerated',
      'true'
    )
  })

  it('fits the observed market cluster and its return point', () => {
    const fitted = fitObservedExecutionNodes({
      nodes: model.nodes,
      viewportWidth: 1000,
      viewportHeight: 600,
      maxZoom: 0.86,
    })
    expect(fitted?.zoom).toBeGreaterThanOrEqual(0.58)
    expect(fitted?.zoom).toBeLessThanOrEqual(0.86)
  })
})
