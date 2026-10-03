// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fetchDataSources, fetchRecordedDataSources } from '@/adapters/api'
import { useLayoutStore } from './store'

vi.mock('@/adapters/api', () => ({ fetchDataSources: vi.fn(), fetchRecordedDataSources: vi.fn() }))

const initialState = useLayoutStore.getState()

describe('useLayoutStore', () => {
  beforeEach(() => {
    useLayoutStore.setState(initialState, true)
  })

  test('opens the data sources panel by default', () => {
    expect(useLayoutStore.getState().rightPanel).toBe('data-sources')
  })

  describe('sessions sidebar collapse model', () => {
    test('opening a research/data-sources panel auto-collapses the sidebar', () => {
      useLayoutStore.getState().closeRightPanel()
      useLayoutStore.getState().openRightPanel('research')

      expect(useLayoutStore.getState()).toMatchObject({
        rightPanel: 'research',
        sessionsCollapsed: true,
        sessionsAutoCollapsed: true,
      })
    })

    test('closing after an auto-collapse restores the sidebar', () => {
      useLayoutStore.getState().openRightPanel('research')
      useLayoutStore.getState().closeRightPanel()

      expect(useLayoutStore.getState()).toMatchObject({
        rightPanel: null,
        sessionsCollapsed: false,
      })
    })

    test('does not auto-restore a sidebar the user collapsed manually', () => {
      useLayoutStore.getState().setSessionsCollapsed(true)
      useLayoutStore.getState().openRightPanel('research')
      useLayoutStore.getState().closeRightPanel()

      expect(useLayoutStore.getState().sessionsCollapsed).toBe(true)
    })
  })

  test('opens and closes the execution workspace with an optional focus', () => {
    useLayoutStore.getState().openExecution('job-1', { referenceId: 'ev-1' })
    expect(useLayoutStore.getState().execution).toEqual({
      jobId: 'job-1',
      focus: { referenceId: 'ev-1' },
    })

    // Citing the same source again is a new focus, so the workspace reopens its node
    const evidence = { referenceId: 'ev-1' }
    useLayoutStore.getState().openExecution('job-1', evidence)
    const first = useLayoutStore.getState().execution?.focus
    useLayoutStore.getState().openExecution('job-1', evidence)
    expect(useLayoutStore.getState().execution?.focus).toEqual(first)
    expect(useLayoutStore.getState().execution?.focus).not.toBe(first)

    useLayoutStore.getState().openExecution('job-2')
    expect(useLayoutStore.getState().execution).toEqual({ jobId: 'job-2', focus: null })

    useLayoutStore.getState().closeExecution()
    expect(useLayoutStore.getState().execution).toBeNull()
  })

  test('toggles a data source on and off', () => {
    useLayoutStore.getState().toggleDataSource('retail.policies')
    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(['retail.policies'])

    useLayoutStore.getState().toggleDataSource('retail.policies')
    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual([])
  })

  describe('fetchDataSources', () => {
    test('stores the sources and enables those enabled by default', async () => {
      vi.mocked(fetchDataSources).mockResolvedValue([
        { id: 'retail.sales', name: 'Market data' },
        { id: 'retail.policies', name: 'News', default_enabled: false },
      ])

      await useLayoutStore.getState().fetchDataSources()

      expect(useLayoutStore.getState()).toMatchObject({
        availableDataSources: [{ id: 'retail.sales' }, { id: 'retail.policies' }],
        enabledDataSourceIds: ['retail.sales'],
        dataSourcesLoading: false,
        dataSourcesError: null,
      })
    })

    test('reads the replay bundle instead of the API when asked', async () => {
      vi.mocked(fetchRecordedDataSources).mockResolvedValue([{ id: 'retail.policies', name: 'News' }])

      await useLayoutStore.getState().fetchDataSources('recordings')

      expect(fetchDataSources).not.toHaveBeenCalled()
      expect(useLayoutStore.getState()).toMatchObject({
        availableDataSources: [{ id: 'retail.policies' }],
        enabledDataSourceIds: ['retail.policies'],
      })
    })

    test('records the error message on failure', async () => {
      vi.mocked(fetchDataSources).mockRejectedValue(new Error('API down'))

      await useLayoutStore.getState().fetchDataSources()

      expect(useLayoutStore.getState()).toMatchObject({
        dataSourcesLoading: false,
        dataSourcesError: 'API down',
      })
    })
  })
})
