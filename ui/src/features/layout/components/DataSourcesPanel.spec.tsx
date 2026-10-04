// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useChatStore } from '@/features/chat'
import { useLayoutStore } from '../store'
import { CATALOG_BUILDING_MESSAGE } from '@/adapters/api'
import { DataSourcesPanel } from './DataSourcesPanel'

vi.mock('@/adapters/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/adapters/api')>()),
  fetchDataSources: vi.fn(),
}))

const initialLayout = useLayoutStore.getState()
const initialChat = useChatStore.getState()

const SOURCES = [
  { id: 'retail.sales', name: 'Sales & Customers', description: 'Stores, orders and returns' },
  { id: 'retail.policies', name: 'Store policies', description: 'Return windows and fees' },
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
    expect(screen.getByText(/Each industry has its own documents and tables/)).toBeInTheDocument()
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

  test('in Your data without files, points to the Files tab instead of "no data sources"', async () => {
    // The Files tab asks the documents API for the workspace's files: none, and no real request
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        Response.json(
          String(url).endsWith('/documents')
            ? { files: [] }
            : { name: 'workspace', file_count: 0, chunk_count: 0, metadata: {} }
        )
      )
    )
    useLayoutStore.setState({
      packId: 'workspace',
      availableDataSources: [],
      enabledDataSourceIds: [],
      dataSourcesPanelTab: 'connections',
    })
    render(<DataSourcesPanel />)
    // Your data opens on Files; back to Connections
    await userEvent.click(screen.getByRole('radio', { name: 'Connections' }))
    expect(screen.getByText(/Your documents and Your tables appear here/)).toBeInTheDocument()
    expect(screen.queryByText('No data sources available')).toBeNull()
    expect(screen.getByText(/No files yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open Files' }))
    expect(useLayoutStore.getState().dataSourcesPanelTab).toBe('files')
    vi.unstubAllGlobals()
  })

  test('says the sources are not ready yet while the catalog is being built', () => {
    useLayoutStore.setState({
      availableDataSources: null,
      dataSourcesError: CATALOG_BUILDING_MESSAGE,
    })
    render(<DataSourcesPanel />)
    expect(screen.getByText('Data sources not ready yet')).toBeInTheDocument()
    expect(screen.getByText(CATALOG_BUILDING_MESSAGE)).toBeInTheDocument()
  })
})
