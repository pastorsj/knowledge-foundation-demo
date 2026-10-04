// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test } from 'vitest'
import { useChatStore, type ChatMessage } from '@/features/chat'
import { useLayoutStore } from '../store'
import { ChatArea } from './ChatArea'

const initialChat = useChatStore.getState()
const initialLayout = useLayoutStore.getState()

const Workspace = () => null

const showMessages = (messages: ChatMessage[]): void => {
  const conversation = {
    id: 's_1',
    userId: 'local',
    title: 'Top stores',
    messages,
    createdAt: new Date(),
    updatedAt: new Date(),
  }
  useChatStore.setState({ currentConversation: conversation, conversations: [conversation] })
}

const question: ChatMessage = {
  id: 'q1',
  role: 'user',
  messageType: 'user',
  content: 'Which stores led in Q3?',
  timestamp: new Date(),
}

const answer: ChatMessage = {
  id: 'a1',
  role: 'assistant',
  messageType: 'agent_response',
  content: 'Store 12 led [1].\n\n**References:**\n- [1] Structured query result — evidence `ev-1`',
  timestamp: new Date(),
  deepResearchJobId: 'job-1',
  deepResearchJobStatus: 'success',
}

describe('ChatArea', () => {
  beforeEach(() => {
    useChatStore.setState(initialChat, true)
    useLayoutStore.setState(initialLayout, true)
  })

  test('shows the welcome state for an empty conversation', () => {
    render(<ChatArea />)

    expect(screen.getByText('What do you want to know?')).toBeInTheDocument()
  })

  test('renders each question with its answer', () => {
    showMessages([question, answer])
    render(<ChatArea />)

    expect(screen.getByText('Which stores led in Q3?')).toBeInTheDocument()
    expect(screen.getByText(/Store 12 led/)).toBeInTheDocument()
  })

  test('renders job banners and dismissible error cards', async () => {
    showMessages([
      question,
      {
        id: 'b1',
        role: 'assistant',
        messageType: 'deep_research_banner',
        content: '',
        timestamp: new Date(),
        deepResearchBannerData: { bannerType: 'failure', jobId: 'job-1' },
      },
      {
        id: 'e1',
        role: 'assistant',
        messageType: 'error',
        content: 'boom',
        timestamp: new Date(),
        errorData: { errorCode: 'agent.deep_research_failed', errorMessage: 'boom' },
      },
    ])
    render(<ChatArea />)

    expect(screen.getByText('Run failed')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /close/i }))
    expect(useChatStore.getState().currentConversation?.messages.map((m) => m.id)).toEqual([
      'q1',
      'b1',
    ])
  })

  test('offers no execution actions without an execution view', () => {
    showMessages([question, answer])
    render(<ChatArea />)

    expect(screen.queryByRole('button', { name: /view execution/i })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Open this run in the execution view' })
    ).not.toBeInTheDocument()
  })

  test('opens the answer’s run, or its cited evidence, in the execution view', async () => {
    showMessages([question, answer])
    render(<ChatArea />, { feature: { Workspace } })

    await userEvent.click(screen.getByRole('button', { name: 'View execution for this response' }))
    expect(useLayoutStore.getState().execution).toEqual({ jobId: 'job-1', focus: null })

    await userEvent.click(
      screen.getByRole('button', { name: 'Open this run in the execution view' })
    )
    expect(useLayoutStore.getState().execution).toEqual({
      jobId: 'job-1',
      focus: { referenceId: 'ev-1' },
    })
  })
})
