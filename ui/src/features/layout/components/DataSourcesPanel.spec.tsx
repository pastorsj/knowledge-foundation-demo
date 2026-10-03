// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useChatStore } from '@/features/chat'
import { useLayoutStore } from '../store'
import { DataSourcesPanel } from './DataSourcesPanel'

vi.mock('@/adapters/api', () => ({ fetchDataSources: vi.fn() }))

const initialLayout = useLayoutStore.getState()
const initialChat = useChatStore.getState()

const SOURCES = [
  { id: 'retail.sales', name: 'Market data', description: 'Prices and volumes' },
  { id: 'retail.policies', name: 'Store policies', description: 'Reviewed news' },
]

describe('DataSourcesPanel', () => {
  beforeEach(() => {
    useChatStore.setState(initialChat, true)
    useChatStore.getState().setCurrentUser('local')
    useLayoutStore.setState(
      {
        ...initialLayout,
        availableDataSources: SOURCES,
        enabledDataSourceIds: ['retail.sales'],
      },
      true
    )
  })

  test('lists the sources with the enabled count', () => {
    render(<DataSourcesPanel />)

    expect(screen.getByText('Individual Connections (2)')).toBeInTheDocument()
    expect(
      screen.getByText(/Each industry has its own documents and tables/)
    ).toBeInTheDocument()
    expect(screen.getByText('Store policies')).toBeInTheDocument()
    expect(screen.getByText(/1 of 2 available connections enabled/)).toBeInTheDocument()
  })

  test('enables a source and saves the selection to the conversation', async () => {
    render(<DataSourcesPanel />)

    await userEvent.click(screen.getByRole('button', { name: 'Store policies: disabled' }))

    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual([
      'retail.sales',
      'retail.policies',
    ])
    expect(useChatStore.getState().currentConversation?.enabledDataSourceIds).toEqual([
      'retail.sales',
      'retail.policies',
    ])
  })

  test('the master switch turns every source off, then on again', async () => {
    render(<DataSourcesPanel />)
    expect(screen.getByText('Disable Selected')).toBeInTheDocument()

    // KUI puts the switch's aria-label on its wrapper; the master switch comes first.
    await userEvent.click(screen.getAllByRole('switch')[0])

    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual([])
    expect(screen.getByText('Enable Compatible')).toBeInTheDocument()

    await userEvent.click(screen.getAllByRole('switch')[0])

    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(SOURCES.map((s) => s.id))
  })

  test('offers a retry when the sources could not be loaded', async () => {
    const fetchDataSources = vi.fn()
    useLayoutStore.setState({
      availableDataSources: null,
      dataSourcesError: 'API down',
      fetchDataSources,
    })
    render(<DataSourcesPanel />)

    expect(screen.getByText('Unable to load data sources')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Retry loading data sources' }))
    expect(fetchDataSources).toHaveBeenCalledOnce()
  })
})
