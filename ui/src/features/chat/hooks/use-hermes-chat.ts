// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * useHermesChat Hook
 *
 * Sends a question to the Hermes agent. Every question is a durable job on
 * the API: this hook records the question, creates the answer placeholder
 * with a caller-chosen job ID, and submits the job. `useDeepResearch` then
 * follows the job's stream until it settles the answer.
 */

'use client'

import { useCallback, useEffect, useRef } from 'react'
import { v4 as uuidv4 } from 'uuid'
import { ApiRequestError, submitJob } from '@/adapters/api'
import { useLayoutStore } from '@/features/layout/store'
import { getDeepResearchJobLoadFailureKind } from '../lib/deep-research-errors'
import { useChatStore } from '../store'

interface UseHermesChatReturn {
  /** Submit a question as a new job in the current conversation */
  sendMessage: (content: string) => void
  /** Cancel the running job */
  stop: () => void
}

/**
 * Whether a failed submission may still have been admitted. Only a lost
 * answer leaves that open: no response at all, or a 502-504 from the UI
 * proxy (or one in front of it) that never reached the API. An answer from
 * the API itself, such as its 503 while it starts, means no job was created.
 */
const isSubmissionOutcomeUnknown = (error: unknown): boolean =>
  error instanceof ApiRequestError
    ? !error.fromApi && error.status >= 502 && error.status <= 504
    : getDeepResearchJobLoadFailureKind(error) === 'backend_unreachable'

export const useHermesChat = (): UseHermesChatReturn => {
  // A page that is being hidden must not overwrite the recovery record with a
  // transport error: the submission may still be admitted server-side.
  const acceptsResultsRef = useRef(true)

  useEffect(() => {
    acceptsResultsRef.current = true
    const handlePageHide = (): void => {
      acceptsResultsRef.current = false
    }
    const handlePageShow = (): void => {
      acceptsResultsRef.current = true
    }
    window.addEventListener('pagehide', handlePageHide)
    window.addEventListener('pageshow', handlePageShow)
    return () => {
      acceptsResultsRef.current = false
      window.removeEventListener('pagehide', handlePageHide)
      window.removeEventListener('pageshow', handlePageShow)
    }
  }, [])

  const sendMessage = useCallback((rawContent: string) => {
    const content = rawContent.trim()
    if (!content) return

    const { enabledDataSourceIds: dataSources, packId } = useLayoutStore.getState()
    const store = useChatStore.getState()
    store.addUserMessage(content, { enabledDataSources: dataSources })

    const conversationId = useChatStore.getState().currentConversation?.id
    if (!conversationId) {
      store.addErrorCard('system.unknown', 'No active conversation')
      return
    }

    // Persist the job identity before submission so a reload between the
    // request and its response can still recover the run.
    const jobId = uuidv4()
    const messageId = store.addAgentResponseWithMeta('', {
      deepResearchJobId: jobId,
      deepResearchJobStatus: 'submitted',
      isDeepResearchActive: true,
    })
    store.addDeepResearchBanner('starting', jobId)
    store.setCurrentStatus('thinking')
    store.setStreaming(true)

    submitJob({ input: content, conversationId, dataSources, jobId, packId })
      .then(({ job_id: admittedJobId }) => {
        if (!acceptsResultsRef.current) return
        if (admittedJobId !== jobId) {
          throw new Error('Research admission returned an unexpected job identifier.')
        }
        const current = useChatStore.getState()
        if (current.currentConversation?.id !== conversationId) return
        current.startDeepResearch(jobId, messageId)
        current.setLoading(false)
      })
      .catch((error: unknown) => {
        if (!acceptsResultsRef.current) return
        const current = useChatStore.getState()
        // A transport error cannot tell whether the job was admitted; recover
        // through the durable job status instead of reporting a false failure.
        if (isSubmissionOutcomeUnknown(error)) {
          current.setStreaming(false)
          current.setLoading(false)
          void current.reconnectToActiveJob()
          return
        }
        current.patchConversationMessage(conversationId, messageId, {
          deepResearchJobStatus: 'failure',
          isDeepResearchActive: false,
        })
        // No job was created, so it has no banner: as in the original demo
        // UI, the error card alone reports the refusal.
        current.addDeepResearchBanner(null, jobId, conversationId)
        current.addErrorCard(
          'agent.response_failed',
          error instanceof Error ? error.message : 'Research could not be started.'
        )
        current.setCurrentStatus('error')
        current.setStreaming(false)
        current.setLoading(false)
      })
  }, [])

  const stop = useCallback(() => {
    const store = useChatStore.getState()
    if (!store.isDeepResearchStreaming) {
      store.setStreaming(false)
      store.setLoading(false)
      return
    }
    store.cancelActiveDeepResearchJob().catch((error: unknown) => {
      useChatStore
        .getState()
        .addErrorCard(
          'agent.response_failed',
          error instanceof Error ? error.message : 'The active run could not be stopped.'
        )
    })
  }, [])

  return { sendMessage, stop }
}
