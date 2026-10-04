// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * NoSourcesBanner Component
 *
 * Displays a warning banner when no data sources are enabled: the agent answers
 * only from the enabled ones. In Your data before any upload is ready, it says
 * how to add some, with a button to the Files tab.
 *
 * Dismissable by user. Dismiss state resets when a source is enabled again
 * so the banner can reappear if the user later removes all sources.
 */

'use client'

import { type FC, useState, useEffect, useRef } from 'react'
import { Banner, Button, Flex } from '@/adapters/ui'
import { useLayoutStore } from '@/features/layout/store'

const WARNING_MESSAGE =
  'No data sources selected. The agent answers only from the sources you enable in Data Sources.'
const NO_FILES_MESSAGE =
  'Your data has no files yet. Upload documents or tables in the Files tab to ask about them.'

/** Opens the data sources panel on Your data's Files tab */
const openFiles = (): void => {
  const { setDataSourcesPanelTab, openRightPanel } = useLayoutStore.getState()
  setDataSourcesPanelTab('files')
  openRightPanel('data-sources')
}

export const NoSourcesBanner: FC = () => {
  const [isDismissedByUser, setIsDismissedByUser] = useState(false)
  const shouldShow = useLayoutStore(
    (state) => state.availableDataSources !== null && state.enabledDataSourceIds.length === 0
  )
  const noFiles = useLayoutStore(
    (state) => state.packId === 'workspace' && state.availableDataSources?.length === 0
  )
  const prevShouldShowRef = useRef(shouldShow)

  useEffect(() => {
    if (prevShouldShowRef.current && !shouldShow) {
      setIsDismissedByUser(false)
    }
    prevShouldShowRef.current = shouldShow
  }, [shouldShow])

  if (!shouldShow || isDismissedByUser) return null

  return (
    <div className="mx-auto w-full max-w-3xl px-4">
      <Banner status="warning" kind="inline" onClose={() => setIsDismissedByUser(true)}>
        {noFiles ? (
          <Flex align="center" gap="3" className="flex-wrap">
            <span>{NO_FILES_MESSAGE}</span>
            <Button kind="secondary" size="small" onClick={openFiles}>
              Open Files
            </Button>
          </Flex>
        ) : (
          WARNING_MESSAGE
        )}
      </Banner>
    </div>
  )
}
