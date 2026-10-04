// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test } from 'vitest'
import { useLayoutStore } from '@/features/layout/store'
import { render, screen } from '@/test-utils'
import { NoSourcesBanner } from './NoSourcesBanner'

describe('NoSourcesBanner', () => {
  beforeEach(() => {
    useLayoutStore.setState({
      packId: 'retail',
      availableDataSources: [{ id: 'retail.sales', name: 'Sales & Customers' }],
      enabledDataSourceIds: [],
      rightPanel: null,
      dataSourcesPanelTab: 'connections',
    })
  })

  test('says the agent answers only from the enabled sources', () => {
    render(<NoSourcesBanner />)
    expect(
      screen.getByText(/The agent answers only from the sources you enable in Data Sources/)
    ).toBeInTheDocument()
  })

  test('in Your data without files, says how to add some and opens the Files tab', async () => {
    useLayoutStore.setState({ packId: 'workspace', availableDataSources: [] })
    render(<NoSourcesBanner />)
    expect(screen.getByText(/Your data has no files yet/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Open Files' }))
    expect(useLayoutStore.getState()).toMatchObject({
      rightPanel: 'data-sources',
      dataSourcesPanelTab: 'files',
    })
  })

  test('is hidden once a source is enabled', () => {
    useLayoutStore.setState({ enabledDataSourceIds: ['retail.sales'] })
    render(<NoSourcesBanner />)
    expect(screen.queryByText(/No data sources selected/)).toBeNull()
  })
})
