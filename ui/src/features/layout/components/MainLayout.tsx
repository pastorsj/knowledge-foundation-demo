// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * MainLayout Component
 *
 * The main application layout container that orchestrates:
 * - AppBar (top)
 * - SessionsPanel (left, collapsible push rail)
 * - ChatArea + InputArea (center, responsive width)
 * - ResearchPanel (right, pushes content)
 * - DataSourcesPanel (right, push panel)
 * - the execution workspace, when a run is opened from an answer
 *
 * The data pack's recordings are listed under "Recorded", beside the browser's
 * own sessions ("My sessions"), as in the original demo UI. A recorded session
 * is read only: its composer and data source selection are disabled. Replay
 * mode has the recordings only.
 */

'use client'

import { type FC, useCallback, useEffect, useMemo, useRef } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useShallow } from 'zustand/react/shallow'
import { Flex } from '@/adapters/ui'
import { useAppConfig, useExecutionFeature } from '@/shared/context'
import { cn } from '@/shared/lib/cn'
import { AppBar } from './AppBar'
import { IndustrySelect, rememberPack } from './IndustrySelect'
import { SessionsPanel, type RecordedCollection } from './SessionsPanel'
import { ChatArea } from './ChatArea'
import { InputArea } from './InputArea'
import { ResearchPanel } from './ResearchPanel'
import { DataSourcesPanel } from './DataSourcesPanel'
import { useChatStore, useDeepResearch, NoSourcesBanner, type ChatMessage } from '@/features/chat'
import {
  hasActiveDeepResearchJob,
  hasCompletedDeepResearchReport,
  sortConversationsByLastUserMessage,
} from '@/features/chat/lib/session-activity'
import { useLayoutStore } from '../store'
import { useRecordedSessions } from '../use-recorded-sessions'
import type { DemoScenario } from '../scenarios'
import { useSessionUrl } from '@/hooks/use-session-url'

/** A question to place in the composer, e.g. a featured question from the landing page. */
export interface InitialQuestion {
  question: string
  sourceIds: string[]
}

interface MainLayoutProps {
  /** The pack the page shows (`?pack=`, the `kf-pack` cookie or DEFAULT_PACK) */
  packId?: string
  initialQuestion?: InitialQuestion | null
  /** The active data pack's questions, offered by the composer's demo scenario picker */
  demoScenarios?: DemoScenario[]
}

/**
 * Keeps the layout store's pack and the URL in step. A pack the page names (on load, or after a
 * navigation) becomes the store's; a pack the store switches to on its own (a restored session of
 * another industry) goes into the URL and the cookie, so the server renders that pack's examples.
 */
const usePackFollowing = (packId: string): void => {
  const storePack = useLayoutStore((state) => state.packId)
  const named = useRef<string | null>(null)
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  useEffect(() => {
    const store = useLayoutStore.getState()
    if (named.current !== packId) {
      named.current = packId
      if (store.packId === null) store.setPackId(packId)
      else if (store.packId !== packId) void store.switchPack(packId)
      return
    }
    if (storePack && storePack !== packId) {
      rememberPack(storePack)
      const params = new URLSearchParams(searchParams?.toString())
      params.set('pack', storePack)
      router.replace(`${pathname}?${params}`)
    }
  }, [packId, pathname, router, searchParams, storePack])
}

/** The question, and its data sources, that started a job in this conversation. */
const turnOfJob = (
  messages: readonly ChatMessage[] | undefined,
  jobId: string | undefined
): { question: string; sourceIds: string[] } | null => {
  const answer = messages?.findIndex((message) => message.deepResearchJobId === jobId) ?? -1
  if (!messages || !jobId || answer < 0) return null
  const asked = messages.slice(0, answer).findLast((message) => message.role === 'user')
  return asked ? { question: asked.content, sourceIds: asked.enabledDataSources ?? [] } : null
}

/**
 * Main application layout with all panels and regions.
 * Chat state is managed via the useChatStore.
 */
export const MainLayout: FC<MainLayoutProps> = ({
  packId = 'retail',
  initialQuestion = null,
  demoScenarios,
}) => {
  usePackFollowing(packId)
  const { mode } = useAppConfig()
  const isReplay = mode === 'replay'
  const { Workspace } = useExecutionFeature()

  const {
    currentConversation,
    conversations,
    isStreaming,
    isDeepResearchStreaming,
    deepResearchOwnerConversationId,
    currentUserId,
  } = useChatStore(
    useShallow((s) => ({
      currentConversation: s.currentConversation,
      conversations: s.conversations,
      isStreaming: s.isStreaming,
      isDeepResearchStreaming: s.isDeepResearchStreaming,
      deepResearchOwnerConversationId: s.deepResearchOwnerConversationId,
      currentUserId: s.currentUserId,
    }))
  )

  const selectConversation = useChatStore((s) => s.selectConversation)
  const startNewSessionDraft = useChatStore((s) => s.startNewSessionDraft)
  const deleteConversation = useChatStore((s) => s.deleteConversation)
  const deleteAllConversations = useChatStore((s) => s.deleteAllConversations)
  const updateConversationTitle = useChatStore((s) => s.updateConversationTitle)

  const openRightPanel = useLayoutStore((s) => s.openRightPanel)
  const execution = useLayoutStore((s) => s.execution)
  const closeExecution = useLayoutStore((s) => s.closeExecution)
  const availableDataSources = useLayoutStore((s) => s.availableDataSources)
  const executionOpen = Boolean(Workspace && execution)
  const executionTurn = useMemo(
    () => turnOfJob(currentConversation?.messages, execution?.jobId),
    [currentConversation?.messages, execution?.jobId]
  )

  // Follows the current job's SSE stream
  useDeepResearch()

  const {
    sessions: recordedSessions,
    status: recordedStatus,
    error: recordedError,
    loadingId: recordedLoadingId,
    open: openRecordedSession,
    retry: retryRecordedSessions,
  } = useRecordedSessions()

  // Sync saved sessions with the ?session= query parameter
  const { updateSessionUrl, clearSessionUrl } = useSessionUrl({ enabled: !isReplay })

  // Place a featured question in a fresh session, with its data sources,
  // once the data sources are known.
  const initialQuestionApplied = useRef(false)
  useEffect(() => {
    if (!initialQuestion || initialQuestionApplied.current || availableDataSources === null) return
    initialQuestionApplied.current = true
    startNewSessionDraft()
    const available = new Set(availableDataSources.map((source) => source.id))
    const layout = useLayoutStore.getState()
    layout.setEnabledDataSources(initialQuestion.sourceIds.filter((id) => available.has(id)))
    layout.setPromptDraft(initialQuestion.question)
    clearSessionUrl()
  }, [initialQuestion, availableDataSources, startNewSessionDraft, clearSessionUrl])

  const handleSelectRecordedSession = useCallback(
    (sessionId: string) => {
      closeExecution()
      clearSessionUrl()
      void openRecordedSession(sessionId)
    },
    [clearSessionUrl, closeExecution, openRecordedSession]
  )

  const handleSelectSession = useCallback(
    (sessionId: string) => {
      if (isReplay) {
        handleSelectRecordedSession(sessionId)
        return
      }
      closeExecution()
      selectConversation(sessionId)
      updateSessionUrl(sessionId)
    },
    [closeExecution, handleSelectRecordedSession, isReplay, selectConversation, updateSessionUrl]
  )

  // A recorded session restored with the page: load its run again, since the
  // execution records are not kept across reloads.
  const reopenedRecording = useRef(false)
  useEffect(() => {
    if (reopenedRecording.current || recordedStatus !== 'ready') return
    reopenedRecording.current = true
    const restored = useChatStore.getState().currentConversation
    if (restored?.readOnly && recordedSessions.some((session) => session.id === restored.id)) {
      void openRecordedSession(restored.id)
    }
  }, [openRecordedSession, recordedSessions, recordedStatus])

  // Start a new unsaved draft session and clear URL until first interaction.
  // Open Data Sources panel so it stays visible (default panel for new sessions).
  const handleNewSession = useCallback(() => {
    startNewSessionDraft()
    clearSessionUrl()
    openRightPanel('data-sources')
  }, [startNewSessionDraft, clearSessionUrl, openRightPanel])

  // Wrap deleteConversation to clear URL if deleting current session
  const handleDeleteSession = useCallback(
    (sessionId: string) => {
      const wasCurrentSession = currentConversation?.id === sessionId
      deleteConversation(sessionId)
      if (wasCurrentSession) {
        clearSessionUrl()
      }
    },
    [deleteConversation, currentConversation?.id, clearSessionUrl]
  )

  // Delete all sessions for the current user
  const handleDeleteAllSessions = useCallback(() => {
    deleteAllConversations()
    clearSessionUrl()
  }, [deleteAllConversations, clearSessionUrl])

  const sessions = useMemo(() => {
    // Replay mode has no sessions of its own: its recordings are under Recorded
    if (isReplay) return []
    return sortConversationsByLastUserMessage(
      currentUserId ? conversations.filter((c) => c.userId === currentUserId) : []
    ).map((conv) => ({
      id: conv.id,
      title: conv.title,
      date: conv.updatedAt,
      hasActiveDeepResearch:
        hasActiveDeepResearchJob(conv.messages) ||
        (isDeepResearchStreaming && deepResearchOwnerConversationId === conv.id),
      hasCompletedReport: hasCompletedDeepResearchReport(conv.messages),
    }))
  }, [
    isReplay,
    conversations,
    currentUserId,
    isDeepResearchStreaming,
    deepResearchOwnerConversationId,
  ])

  const isRecordedSession = isReplay || currentConversation?.readOnly === true
  const recordedConversationId = currentConversation?.readOnly ? currentConversation.id : null
  // Live mode lists the recordings once they have loaded, if the pack has any.
  const showRecorded = isReplay || (recordedStatus === 'ready' && recordedSessions.length > 0)
  const recorded = useMemo<RecordedCollection | undefined>(
    () =>
      showRecorded
        ? {
            sessions: recordedSessions,
            status: recordedStatus,
            error: recordedError,
            selectedId: recordedConversationId,
            loadingId: recordedLoadingId,
            onSelect: handleSelectRecordedSession,
            onRetry: retryRecordedSessions,
          }
        : undefined,
    [
      showRecorded,
      recordedSessions,
      recordedStatus,
      recordedError,
      recordedConversationId,
      recordedLoadingId,
      handleSelectRecordedSession,
      retryRecordedSessions,
    ]
  )

  return (
    <Flex direction="col" className="h-screen min-w-[768px] overflow-x-auto overflow-y-hidden">
      {/* AppBar - Fixed at top */}
      <AppBar
        sessionTitle={currentConversation?.title}
        onNewSession={executionOpen ? closeExecution : handleNewSession}
        newSessionActionLabel={executionOpen ? 'Back to answer' : 'Create new session'}
        isNewSessionDisabled={executionOpen ? false : isReplay || isStreaming}
        isDataSourceSelectionDisabled={isRecordedSession}
        industrySelect={<IndustrySelect packId={packId} />}
      />

      {/* Main content area: in-flow panels reflow the center column (push, not overlay) */}
      <div className="relative flex flex-1 overflow-hidden">
        {!executionOpen && (
          <SessionsPanel
            sessions={sessions}
            selectedSessionId={currentConversation?.id}
            onSelectSession={handleSelectSession}
            onNewSession={handleNewSession}
            onDeleteSession={handleDeleteSession}
            onDeleteAllSessions={handleDeleteAllSessions}
            onRenameSession={updateConversationTitle}
            recorded={recorded}
            readOnly={isReplay}
          />
        )}

        {/* Center Content: Chat + Input */}
        <div
          className={cn(
            'flex min-w-0 flex-col overflow-hidden',
            executionOpen
              ? 'border-base w-[clamp(20rem,27vw,28rem)] flex-none border-r 2xl:w-[30rem]'
              : 'flex-1'
          )}
        >
          <ChatArea compact={executionOpen} />
          {!isReplay && <NoSourcesBanner />}
          <InputArea scenarios={demoScenarios} showDemoScenarios={!executionOpen} />
        </div>

        {Workspace && execution ? (
          <div className="min-w-0 flex-1 overflow-hidden">
            <Workspace
              key={execution.jobId}
              jobId={execution.jobId}
              focus={execution.focus}
              question={executionTurn?.question ?? null}
              sourceIds={executionTurn?.sourceIds}
              onClose={closeExecution}
            />
          </div>
        ) : (
          <>
            {!isRecordedSession && <DataSourcesPanel />}
            <ResearchPanel />
          </>
        )}
      </div>
    </Flex>
  )
}
