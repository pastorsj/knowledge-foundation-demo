// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The data viewer outside a run: the selected pack's structured sources (Your
 * data's tables, once an upload has loaded them), over the page, in the
 * execution workspace's look. Escape or the close button returns.
 */

'use client'

import { useMemo, type ReactNode } from 'react'
import { useLayoutStore } from '@/features/layout/store'
import styles from '../execution-workspace.module.css'
import { DatabaseBrowser, type StructuredSource } from './DatabaseBrowser'

export const DataViewerDialog = ({ onClose }: { onClose: () => void }): ReactNode => {
  const available = useLayoutStore((state) => state.availableDataSources)
  const sources = useMemo<StructuredSource[]>(
    () =>
      (available ?? []).flatMap((source) =>
        source.database_name
          ? [{ id: source.id, name: source.name, databaseName: source.database_name }]
          : []
      ),
    [available]
  )
  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/50 p-6"
      data-testid="data-viewer-dialog"
    >
      <div className={`${styles.workspace} ${styles.graphRegion} w-full max-w-6xl`}>
        <DatabaseBrowser
          detail={{ id: 'structured-database' }}
          cursor="0"
          sources={sources}
          onClose={onClose}
        />
      </div>
    </div>
  )
}
