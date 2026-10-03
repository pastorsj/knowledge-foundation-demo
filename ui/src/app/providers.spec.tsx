// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { fetchDataSources, getJobStatus } from '@/adapters/api'
import { useChatStore } from '@/features/chat/store'
import type { Conversation } from '@/features/chat/types'
import { useLayoutStore } from '@/features/layout'
import { Providers } from './providers'

vi.mock('@/adapters/api', () => ({
  fetchDataSources: vi.fn(),
  getJobStatus: vi.fn(),
}))

const initialChat = useChatStore.getState()
const SPEECH_OFF = { enabled: false, maxSeconds: 60 }
const initialLayout = useLayoutStore.getState()

/** Saves a live session whose job was running when the page closed, then reloads the store. */
const loadSavedSessionWithRunningJob = async (): Promise<void> => {
  const saved: Conversation = {
    id: 's_saved',
    userId: 'local',
    title: 'Which assets led?',
    createdAt: new Date(),
    updatedAt: new Date(),
    messages: [
      {
        id: 'question',
        role: 'user',
        content: 'Which assets led?',
        timestamp: new Date(),
        messageType: 'user',
      },
      {
        id: 'answer',
        role: 'assistant',
        content: '',
        timestamp: new Date(),
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'running',
        isDeepResearchActive: true,
      },
    ],
  }
  useChatStore.setState({ currentUserId: 'local', conversations: [saved] })
  await useChatStore.persist.rehydrate()
}

const savedJobStatus = () =>
  useChatStore.getState().conversations[0]?.messages[1]?.deepResearchJobStatus

describe('Providers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchDataSources).mockResolvedValue([{ id: 'retail.policies', name: 'News' }])
    useChatStore.setState(initialChat, true)
    useLayoutStore.setState(initialLayout, true)
  })

  test('live mode selects the local user and loads the data sources', async () => {
    render(
      <Providers config={{ mode: 'live', phoenixUrl: null, speechInput: SPEECH_OFF }}>
        content
      </Providers>
    )

    await waitFor(() =>
      expect(useLayoutStore.getState().availableDataSources).toEqual([
        { id: 'retail.policies', name: 'News' },
      ])
    )
    expect(useChatStore.getState().currentUserId).toBe('local')
  })

  test('live mode settles saved jobs that ended while the page was closed', async () => {
    vi.mocked(getJobStatus).mockResolvedValue({ job_id: 'job-1', status: 'failure', error: null })
    await loadSavedSessionWithRunningJob()

    render(
      <Providers config={{ mode: 'live', phoenixUrl: null, speechInput: SPEECH_OFF }}>
        content
      </Providers>
    )

    await waitFor(() => expect(savedJobStatus()).toBe('failure'))
    expect(getJobStatus).toHaveBeenCalledWith('job-1')
  })

  test('replay mode never calls the API, even for saved live jobs', async () => {
    await loadSavedSessionWithRunningJob()

    render(
      <Providers config={{ mode: 'replay', phoenixUrl: null, speechInput: SPEECH_OFF }}>
        content
      </Providers>
    )

    await waitFor(() => expect(useChatStore.getState().currentUserId).toBe('local'))
    expect(fetchDataSources).not.toHaveBeenCalled()
    expect(getJobStatus).not.toHaveBeenCalled()
    expect(savedJobStatus()).toBe('running')
  })
})
