// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { beforeEach, describe, expect, test, vi } from 'vitest'
import {
  CATALOG_BUILDING_MESSAGE,
  DataSourcesError,
  fetchDataSources,
  fetchRecordedDataSources,
} from '@/adapters/api'
import { SOURCES_RETRY_MS, useLayoutStore } from './store'

vi.mock('@/adapters/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/adapters/api')>()),
  fetchDataSources: vi.fn(),
  fetchRecordedDataSources: vi.fn(),
}))
const startNewSessionDraft = vi.fn()
vi.mock('@/features/chat/store', () => ({
  useChatStore: { getState: () => ({ startNewSessionDraft }) },
}))

const initialState = useLayoutStore.getState()

describe('useLayoutStore', () => {
  beforeEach(() => {
    useLayoutStore.setState({ ...initialState, packId: 'retail' }, true)
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
        { id: 'retail.sales', name: 'Sales & Customers' },
        { id: 'retail.policies', name: 'News', default_enabled: false },
      ])

      await useLayoutStore.getState().fetchDataSources()

      expect(fetchDataSources).toHaveBeenCalledWith('retail')
      expect(useLayoutStore.getState()).toMatchObject({
        availableDataSources: [{ id: 'retail.sales' }, { id: 'retail.policies' }],
        enabledDataSourceIds: ['retail.sales'],
        dataSourcesLoading: false,
        dataSourcesError: null,
      })
    })

    test('reads the replay bundle instead of the API when asked', async () => {
      vi.mocked(fetchRecordedDataSources).mockResolvedValue([
        { id: 'retail.policies', name: 'News' },
      ])

      await useLayoutStore.getState().fetchDataSources('recordings')

      expect(fetchDataSources).not.toHaveBeenCalled()
      expect(fetchRecordedDataSources).toHaveBeenCalledWith('retail')
      expect(useLayoutStore.getState()).toMatchObject({
        availableDataSources: [{ id: 'retail.policies' }],
        enabledDataSourceIds: ['retail.policies'],
        dataSourcesFrom: 'recordings',
      })
    })

    test('loads nothing until a page names its pack', async () => {
      useLayoutStore.setState({ packId: null })
      await useLayoutStore.getState().fetchDataSources()
      expect(fetchDataSources).not.toHaveBeenCalled()
    })

    test('records the error message on failure', async () => {
      vi.mocked(fetchDataSources).mockRejectedValue(new Error('API down'))

      await useLayoutStore.getState().fetchDataSources()

      expect(useLayoutStore.getState()).toMatchObject({
        dataSourcesLoading: false,
        dataSourcesError: 'API down',
      })
    })

    test('while the catalog is being built (503), says so and tries again every 5 s by itself', async () => {
      vi.useFakeTimers()
      try {
        vi.mocked(fetchDataSources)
          .mockRejectedValueOnce(new DataSourcesError(CATALOG_BUILDING_MESSAGE, 503))
          .mockResolvedValueOnce([{ id: 'retail.sales', name: 'Sales' }])
        await useLayoutStore.getState().fetchDataSources()
        expect(useLayoutStore.getState().dataSourcesError).toBe(CATALOG_BUILDING_MESSAGE)

        // The retry keeps the message up (no spinner) until the sources come
        await vi.advanceTimersByTimeAsync(SOURCES_RETRY_MS - 1)
        expect(fetchDataSources).toHaveBeenCalledTimes(1)
        await vi.advanceTimersByTimeAsync(1)
        expect(fetchDataSources).toHaveBeenCalledTimes(2)
        expect(useLayoutStore.getState()).toMatchObject({
          dataSourcesError: null,
          dataSourcesLoading: false,
          availableDataSources: [{ id: 'retail.sales', name: 'Sales' }],
        })
        await vi.advanceTimersByTimeAsync(SOURCES_RETRY_MS * 3)
        expect(fetchDataSources).toHaveBeenCalledTimes(2)
      } finally {
        vi.useRealTimers()
      }
    })

    test('does not try again by itself after an error that will not pass (a 404)', async () => {
      vi.useFakeTimers()
      try {
        vi.mocked(fetchDataSources).mockRejectedValue(new DataSourcesError('Unknown pack', 404))
        await useLayoutStore.getState().fetchDataSources()
        await vi.advanceTimersByTimeAsync(SOURCES_RETRY_MS * 2)
        expect(fetchDataSources).toHaveBeenCalledTimes(1)
      } finally {
        vi.useRealTimers()
      }
    })

    test('refreshes the sources every 5 s while one is ingesting, then stops', async () => {
      vi.useFakeTimers()
      try {
        vi.mocked(fetchDataSources)
          .mockResolvedValueOnce([{ id: 'retail.sales', name: 'Sales', status: 'ingesting' }])
          .mockResolvedValueOnce([{ id: 'retail.sales', name: 'Sales', status: 'ready' }])
        await useLayoutStore.getState().fetchDataSources()
        await vi.advanceTimersByTimeAsync(SOURCES_RETRY_MS)
        expect(fetchDataSources).toHaveBeenCalledTimes(2)
        expect(useLayoutStore.getState().availableDataSources?.[0].status).toBe('ready')
        await vi.advanceTimersByTimeAsync(SOURCES_RETRY_MS * 2)
        expect(fetchDataSources).toHaveBeenCalledTimes(2)
      } finally {
        vi.useRealTimers()
      }
    })
  })

  describe('switchPack', () => {
    test('replaces the sources, closes the execution view and starts a new draft', async () => {
      vi.mocked(fetchDataSources).mockResolvedValue([
        { id: 'manufacturing.sops', name: 'Procedures' },
        { id: 'manufacturing.plant', name: 'Plant', default_enabled: false },
      ])
      useLayoutStore.setState({
        availableDataSources: [{ id: 'retail.sales', name: 'Sales' }],
        enabledDataSourceIds: ['retail.sales'],
        execution: { jobId: 'job-1', focus: null },
        dataSourcesPanelTab: 'files',
      })

      const switching = useLayoutStore.getState().switchPack('manufacturing')
      // Nothing of the pack left behind shows while the new one loads
      expect(useLayoutStore.getState()).toMatchObject({
        packId: 'manufacturing',
        availableDataSources: null,
        enabledDataSourceIds: [],
        execution: null,
        dataSourcesLoading: true,
        dataSourcesPanelTab: 'connections',
      })
      await switching

      expect(fetchDataSources).toHaveBeenCalledWith('manufacturing')
      expect(startNewSessionDraft).toHaveBeenCalledOnce()
      expect(useLayoutStore.getState()).toMatchObject({
        availableDataSources: [{ id: 'manufacturing.sops' }, { id: 'manufacturing.plant' }],
        enabledDataSourceIds: ['manufacturing.sops'],
      })
    })

    test('restores a session’s own pack with its sources, keeping the session', async () => {
      vi.mocked(fetchDataSources).mockResolvedValue([
        { id: 'healthcare.records', name: 'Records' },
        { id: 'healthcare.policies', name: 'Policies' },
      ])

      await useLayoutStore.getState().switchPack('healthcare', {
        draft: false,
        enabledIds: ['healthcare.policies', 'retail.sales'],
      })

      expect(startNewSessionDraft).not.toHaveBeenCalled()
      expect(useLayoutStore.getState()).toMatchObject({
        packId: 'healthcare',
        enabledDataSourceIds: ['healthcare.policies'],
      })
    })

    test('keeps the answer of the last pack chosen when two loads race', async () => {
      let answerRetail: (value: { id: string; name: string }[]) => void = () => undefined
      vi.mocked(fetchDataSources)
        .mockImplementationOnce(() => new Promise((resolve) => (answerRetail = resolve)))
        .mockResolvedValueOnce([{ id: 'manufacturing.sops', name: 'Procedures' }])

      const slow = useLayoutStore.getState().switchPack('retail', { draft: false })
      await useLayoutStore.getState().switchPack('manufacturing', { draft: false })
      answerRetail([{ id: 'retail.sales', name: 'Sales' }])
      await slow

      expect(useLayoutStore.getState()).toMatchObject({
        packId: 'manufacturing',
        availableDataSources: [{ id: 'manufacturing.sops' }],
      })
    })
  })

  describe('refreshDataSources', () => {
    test('keeps the selection and enables a source that just appeared', async () => {
      useLayoutStore.setState({
        packId: 'workspace',
        availableDataSources: [
          { id: 'workspace.documents', name: 'Your documents' },
          { id: 'workspace.notes', name: 'Notes' },
        ],
        enabledDataSourceIds: ['workspace.documents'],
      })
      vi.mocked(fetchDataSources).mockResolvedValue([
        { id: 'workspace.documents', name: 'Your documents' },
        { id: 'workspace.notes', name: 'Notes' },
        { id: 'workspace.tables', name: 'Your tables' },
      ])

      await useLayoutStore.getState().refreshDataSources()

      expect(fetchDataSources).toHaveBeenCalledWith('workspace')
      expect(useLayoutStore.getState().enabledDataSourceIds).toEqual([
        'workspace.documents',
        'workspace.tables',
      ])
    })
  })
})
