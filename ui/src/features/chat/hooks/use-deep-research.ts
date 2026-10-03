// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useDeepResearch Hook
 *
 * Follows the SSE stream of the current job. Connects when a job starts (or
 * is reconnected after a reload), forwards every record to the execution
 * view, keeps the final answer, and settles the answer message when the job
 * reaches a terminal status.
 */

'use client'

import { useEffect } from 'react'
import { createDeepResearchClient } from '@/adapters/api'
import { useLayoutStore } from '@/features/layout/store'
import { useExecutionFeature } from '@/shared/context'
import { useChatStore } from '../store'

export const useDeepResearch = (): void => {
  const { onJobEvent, ActivityPanel } = useExecutionFeature()
  const jobId = useChatStore((s) => s.deepResearchJobId)
  const isStreaming = useChatStore((s) => s.isDeepResearchStreaming)

  useEffect(() => {
    if (!jobId || !isStreaming) return

    const store = useChatStore.getState()
    // Events of a job the user navigated away from must not touch the store.
    const isCurrentJob = (): boolean => {
      const state = useChatStore.getState()
      return state.isDeepResearchStreaming && state.deepResearchJobId === jobId
    }
    const stopOnTransportFailure = (message: string): void => {
      if (!isCurrentJob()) return
      store.finishDeepResearch('failure', `Lost connection to the run: ${message}`)
    }

    const client = createDeepResearchClient({
      jobId,
      callbacks: {
        onEvent: onJobEvent,
        onStreamStart: () => {
          if (isCurrentJob()) store.setCurrentStatus('researching')
        },
        onJobStatus: (status, error) => {
          if (!isCurrentJob()) return
          if (status === 'submitted' || status === 'running') {
            store.updateDeepResearchStatus(status)
          } else {
            store.finishDeepResearch(status, error)
          }
        },
        onFinalReport: (content) => {
          if (!isCurrentJob()) return
          store.setReportContent(content)
          store.setCurrentStatus('writing')
        },
        onError: (error) => stopOnTransportFailure(error.message),
        onDisconnect: () => stopOnTransportFailure('the stream was closed'),
      },
    })

    // Defer the connection so React StrictMode's mount/unmount/mount cycle
    // opens only one stream.
    const connectTimer = setTimeout(() => {
      client.connect()
      if (ActivityPanel) useLayoutStore.getState().openRightPanel('research')
    }, 50)

    return () => {
      clearTimeout(connectTimer)
      client.disconnect()
    }
  }, [jobId, isStreaming, onJobEvent, ActivityPanel])
}
