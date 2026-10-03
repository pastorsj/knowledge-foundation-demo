// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Layout Store
 *
 * Zustand store for managing the main app layout state.
 * Controls sidebar visibility and panel states.
 */

import { create } from 'zustand'
import { devtools } from 'zustand/middleware'
import { fetchDataSources, fetchRecordedDataSources } from '@/adapters/api'
import type { DataSourceFromAPI } from '@/adapters/api'
import type { LayoutState, LayoutStore } from './types'

const initialState: LayoutState = {
  sessionsCollapsed: false,
  sessionsAutoCollapsed: false,
  rightPanel: 'data-sources',
  dataSourcesPanelTab: 'connections',
  packId: null,
  execution: null,
  enabledDataSourceIds: [], // Populated when data sources are fetched
  theme: 'system',
  availableDataSources: null,
  dataSourcesLoading: false,
  dataSourcesError: null,
  dataSourcesFrom: 'api',
  promptDraft: null,
}

/** The sources a pack starts with enabled */
const defaultEnabled = (sources: readonly DataSourceFromAPI[]): string[] =>
  sources.filter((source) => source.default_enabled !== false).map((source) => source.id)

/** Bumped by every fetch, so a slow answer for a pack left behind never lands */
let fetchGeneration = 0

export const useLayoutStore = create<LayoutStore>()(
  devtools(
    (set, get) => ({
      ...initialState,

      toggleSessionsSidebar: () =>
        set(
          (state) => ({
            sessionsCollapsed: !state.sessionsCollapsed,
            sessionsAutoCollapsed: false,
          }),
          false,
          'toggleSessionsSidebar'
        ),

      setSessionsCollapsed: (collapsed) =>
        set(
          { sessionsCollapsed: collapsed, sessionsAutoCollapsed: false },
          false,
          'setSessionsCollapsed'
        ),

      openRightPanel: (panel) =>
        set(
          (state) => {
            const collapsesSidebar = panel === 'research' || panel === 'data-sources'
            if (collapsesSidebar && !state.sessionsCollapsed) {
              return { rightPanel: panel, sessionsCollapsed: true, sessionsAutoCollapsed: true }
            }
            if (!collapsesSidebar && state.sessionsAutoCollapsed) {
              return { rightPanel: panel, sessionsCollapsed: false, sessionsAutoCollapsed: false }
            }
            return { rightPanel: panel }
          },
          false,
          'openRightPanel'
        ),

      closeRightPanel: () =>
        set(
          (state) =>
            state.sessionsAutoCollapsed
              ? { rightPanel: null, sessionsCollapsed: false, sessionsAutoCollapsed: false }
              : { rightPanel: null },
          false,
          'closeRightPanel'
        ),

      setDataSourcesPanelTab: (tab) =>
        set({ dataSourcesPanelTab: tab }, false, 'setDataSourcesPanelTab'),

      setPackId: (packId) => set({ packId }, false, 'setPackId'),

      switchPack: async (packId, { draft = true, enabledIds } = {}) => {
        set(
          {
            packId,
            availableDataSources: null,
            enabledDataSourceIds: [],
            execution: null,
            dataSourcesPanelTab: 'connections',
          },
          false,
          'switchPack'
        )
        // Started first, so the fetch is under way (loading) before anything else runs
        const fetching = get().fetchDataSources(get().dataSourcesFrom, enabledIds)
        if (draft) {
          // Imported when used: the chat store imports this one
          const { useChatStore } = await import('@/features/chat/store')
          useChatStore.getState().startNewSessionDraft()
        }
        await fetching
      },

      // A new focus object per call, so citing the same source again reopens its node
      openExecution: (jobId, focus) =>
        set({ execution: { jobId, focus: focus ? { ...focus } : null } }, false, 'openExecution'),

      closeExecution: () => set({ execution: null }, false, 'closeExecution'),

      toggleDataSource: (id) =>
        set(
          (state) => {
            const isEnabled = state.enabledDataSourceIds.includes(id)
            return {
              enabledDataSourceIds: isEnabled
                ? state.enabledDataSourceIds.filter((sourceId) => sourceId !== id)
                : [...state.enabledDataSourceIds, id],
            }
          },
          false,
          'toggleDataSource'
        ),

      setEnabledDataSources: (ids) =>
        set({ enabledDataSourceIds: ids }, false, 'setEnabledDataSources'),

      setTheme: (theme) => set({ theme }, false, 'setTheme'),

      setPromptDraft: (value) => set({ promptDraft: value }, false, 'setPromptDraft'),

      fetchDataSources: async (from = get().dataSourcesFrom, enabledIds) => {
        const { packId } = get()
        if (!packId) return
        const generation = ++fetchGeneration
        set(
          { dataSourcesLoading: true, dataSourcesError: null, dataSourcesFrom: from },
          false,
          'fetchDataSources/start'
        )

        try {
          const sources =
            from === 'recordings'
              ? await fetchRecordedDataSources(packId)
              : await fetchDataSources(packId)
          if (generation !== fetchGeneration) return
          const available = new Set(sources.map((source) => source.id))
          set(
            {
              availableDataSources: sources,
              enabledDataSourceIds: enabledIds
                ? enabledIds.filter((id) => available.has(id))
                : defaultEnabled(sources),
              dataSourcesLoading: false,
            },
            false,
            'fetchDataSources/success'
          )
        } catch (error) {
          if (generation !== fetchGeneration) return
          set(
            {
              dataSourcesLoading: false,
              dataSourcesError:
                error instanceof Error ? error.message : 'Failed to fetch data sources',
            },
            false,
            'fetchDataSources/error'
          )
        }
      },

      refreshDataSources: async () => {
        const { packId, dataSourcesFrom, availableDataSources, enabledDataSourceIds } = get()
        if (!packId) return
        try {
          const sources =
            dataSourcesFrom === 'recordings'
              ? await fetchRecordedDataSources(packId)
              : await fetchDataSources(packId)
          if (get().packId !== packId) return
          // Keep the selection; a source that just appeared (an upload's first table) starts enabled
          const known = new Set((availableDataSources ?? []).map((source) => source.id))
          const available = new Set(sources.map((source) => source.id))
          const appeared = defaultEnabled(sources.filter((source) => !known.has(source.id)))
          set(
            {
              availableDataSources: sources,
              enabledDataSourceIds: [
                ...enabledDataSourceIds.filter((id) => available.has(id)),
                ...appeared.filter((id) => !enabledDataSourceIds.includes(id)),
              ],
            },
            false,
            'refreshDataSources'
          )
        } catch {
          // The sources shown stay as they were; the next refresh tries again
        }
      },
    }),
    { name: 'LayoutStore' }
  )
)
