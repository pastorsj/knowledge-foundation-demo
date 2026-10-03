// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { cancelJob, getJobReport, getJobStatus } from '@/adapters/api'
import { useLayoutStore } from '@/features/layout/store'
import { useChatStore } from './store'
import type { ChatMessage, Conversation } from './types'

vi.mock('@/adapters/api', () => ({
  cancelJob: vi.fn(),
  getJobReport: vi.fn(),
  getJobStatus: vi.fn(),
}))

const initialChat = useChatStore.getState()
const initialLayout = useLayoutStore.getState()

const chat = () => useChatStore.getState()
const messages = (): ChatMessage[] => chat().currentConversation?.messages ?? []
const kinds = (): Array<string | undefined> =>
  messages().map((m) => m.deepResearchBannerData?.bannerType ?? m.messageType)

/** A conversation whose question has a job-backed answer placeholder, as useHermesChat creates it. */
const askQuestion = (jobId = 'job-1'): string => {
  chat().ensureSession()
  chat().addUserMessage('Which assets led?')
  const messageId = chat().addAgentResponseWithMeta('', {
    deepResearchJobId: jobId,
    deepResearchJobStatus: 'submitted',
    isDeepResearchActive: true,
  })
  chat().addDeepResearchBanner('starting', jobId)
  return messageId
}

describe('useChatStore', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChatStore.setState(initialChat, true)
    useLayoutStore.setState(
      {
        ...initialLayout,
        availableDataSources: [
          { id: 'market_analysis_structured', name: 'Market data' },
          { id: 'market_news', name: 'News', default_enabled: false },
        ],
      },
      true
    )
    chat().setCurrentUser('local')
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('sessions', () => {
    test('a new session starts with the default data sources', () => {
      chat().startNewSessionDraft()
      chat().ensureSession()

      expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(['market_analysis_structured'])
      expect(chat().currentConversation?.enabledDataSourceIds).toEqual([
        'market_analysis_structured',
      ])
    })

    test('the first question titles the conversation', () => {
      chat().addUserMessage('q'.repeat(60))
      chat().addUserMessage('And the weakest?')

      expect(chat().currentConversation?.title).toBe(`${'q'.repeat(50)}...`)
      expect(chat().conversations).toHaveLength(1)
    })

    test('selecting a conversation restores its data sources', () => {
      chat().ensureSession()
      chat().saveDataSourcesToConversation(['market_news'])
      const saved = chat().currentConversation!.id
      chat().startNewSessionDraft()

      chat().selectConversation(saved)

      expect(chat().currentConversation?.id).toBe(saved)
      expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(['market_news'])
    })

    test('reopening a conversation whose question was never submitted reports it', () => {
      chat().addUserMessage('Lost question')
      const id = chat().currentConversation!.id
      chat().startNewSessionDraft()

      chat().selectConversation(id)

      expect(messages().at(-1)?.errorData?.errorCode).toBe('agent.response_interrupted')
    })

    test('deleting a conversation cancels its running job', () => {
      vi.mocked(cancelJob).mockResolvedValue({ cancelled: true })
      askQuestion('job-1')

      chat().deleteConversation(chat().currentConversation!.id)

      expect(cancelJob).toHaveBeenCalledWith('job-1')
      expect(chat().conversations).toEqual([])
    })
  })

  describe('job lifecycle', () => {
    test('a successful job replaces its banner with the answer', () => {
      const messageId = askQuestion()
      chat().startDeepResearch('job-1', messageId)
      chat().setReportContent('# Answer')

      chat().finishDeepResearch('success')

      expect(kinds()).toEqual(['user', 'agent_response'])
      expect(messages()[1]).toMatchObject({
        content: '# Answer',
        deepResearchJobStatus: 'success',
        isDeepResearchActive: false,
      })
      expect(chat()).toMatchObject({
        isDeepResearchStreaming: false,
        isStreaming: false,
        currentStatus: 'complete',
      })
    })

    test('a successful job whose answer was not streamed fetches its report', async () => {
      vi.mocked(getJobReport).mockResolvedValue({
        job_id: 'job-1',
        has_report: true,
        report: '# Answer',
      })
      const messageId = askQuestion()
      chat().startDeepResearch('job-1', messageId)

      chat().finishDeepResearch('success')

      await vi.waitFor(() => expect(messages()[1].content).toBe('# Answer'))
      expect(kinds()).toEqual(['user', 'agent_response'])
    })

    test('a failed job without an answer shows a failure banner and the error', () => {
      const messageId = askQuestion()
      chat().startDeepResearch('job-1', messageId)

      chat().finishDeepResearch('failure', 'model unavailable')

      expect(kinds()).toEqual(['user', 'agent_response', 'failure', 'error'])
      expect(messages().at(-1)?.errorData?.errorMessage).toBe('model unavailable')
    })

    test('a job cancelled by the user shows a cancellation banner only', () => {
      const messageId = askQuestion()
      chat().startDeepResearch('job-1', messageId)

      chat().finishDeepResearch('interrupted', 'Cancelled by user')

      expect(kinds()).toEqual(['user', 'agent_response', 'cancelled'])
    })

    test('cancelling settles locally when the stream never confirms', async () => {
      vi.useFakeTimers()
      vi.mocked(cancelJob).mockResolvedValue({ cancelled: true })
      const messageId = askQuestion()
      chat().startDeepResearch('job-1', messageId)

      await chat().cancelActiveDeepResearchJob()
      expect(cancelJob).toHaveBeenCalledWith('job-1')
      expect(chat().isDeepResearchStreaming).toBe(true)

      vi.advanceTimersByTime(5000)

      expect(chat().isDeepResearchStreaming).toBe(false)
      expect(kinds()).toEqual(['user', 'agent_response', 'cancelled'])
    })

    test('a session with a running job is busy', () => {
      askQuestion()

      expect(chat().isSessionBusy(chat().currentConversation!.id)).toBe(true)
      expect(chat().hasAnyBusySession()).toBe(true)
    })
  })

  describe('recovery after a reload', () => {
    test('reconnects to a job that is still running', async () => {
      vi.mocked(getJobStatus).mockResolvedValue({ job_id: 'job-1', status: 'running', error: null })
      askQuestion()

      await chat().reconnectToActiveJob()

      expect(chat()).toMatchObject({
        deepResearchJobId: 'job-1',
        isDeepResearchStreaming: true,
        deepResearchStatus: 'running',
      })
    })

    test('settles a job that finished while the page was closed with its report', async () => {
      vi.mocked(getJobStatus).mockResolvedValue({ job_id: 'job-1', status: 'success', error: null })
      vi.mocked(getJobReport).mockResolvedValue({
        job_id: 'job-1',
        has_report: true,
        report: '# Answer',
      })
      askQuestion()

      await chat().refreshDeepResearchSessionStatuses()

      expect(kinds()).toEqual(['user', 'agent_response'])
      expect(messages()[1].content).toBe('# Answer')
    })

    test('marks a job the API no longer knows as failed and unavailable', async () => {
      vi.mocked(getJobStatus).mockRejectedValue(new Error('Failed to get job status: 404'))
      askQuestion()

      await chat().reconnectToActiveJob()

      expect(messages()[1].deepResearchJobStatus).toBe('failure')
      expect(kinds()).toEqual(['user', 'agent_response', 'expired'])
    })
  })

  describe('recorded sessions', () => {
    test('opens a recording as a read-only conversation that is never saved', () => {
      chat().openRecordedSession({
        id: 'rec-1',
        title: 'Market leaders',
        recordedAt: '2026-09-01T00:00:00Z',
        turns: [
          { question: 'Which assets led?', answer: 'Asset A.', jobId: 'job-1', sourceIds: [] },
          { question: 'Why?', answer: null, jobId: 'job-2', sourceIds: [] },
        ],
      })

      const conversation = chat().currentConversation as Conversation
      expect(conversation.readOnly).toBe(true)
      expect(kinds()).toEqual(['user', 'agent_response', 'user', 'error'])
      expect(messages()[1]).toMatchObject({ content: 'Asset A.', deepResearchJobId: 'job-1' })
      expect(chat().conversations).toEqual([])
    })
  })

  test('persists conversations with the current one stored by ID', () => {
    chat().addUserMessage('Saved question')

    const stored = JSON.parse(localStorage.getItem('aiq-chat-store')!)
    expect(stored.state.currentConversation).toBe(chat().currentConversation!.id)
    expect(stored.state.conversations[0].messages[0].content).toBe('Saved question')
  })
})
