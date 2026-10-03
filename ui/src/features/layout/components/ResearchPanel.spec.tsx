// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useChatStore } from '@/features/chat'
import type { ActivityPanelProps } from '@/shared/context'
import { useLayoutStore } from '../store'
import { ResearchPanel } from './ResearchPanel'

const initialLayout = useLayoutStore.getState()
const initialChat = useChatStore.getState()

const ActivityPanel = ({ jobId, open }: ActivityPanelProps) => (
  <p>{open ? `Activity for ${jobId}` : 'closed'}</p>
)

describe('ResearchPanel', () => {
  beforeEach(() => {
    useLayoutStore.setState(initialLayout, true)
    useChatStore.setState(initialChat, true)
  })

  test('renders nothing without an execution activity panel', () => {
    render(<ResearchPanel />)

    expect(screen.queryByTestId('research-panel-toggle')).not.toBeInTheDocument()
  })

  test('shows the activity panel for the current job when opened', async () => {
    useChatStore.setState({ deepResearchJobId: 'job-1' })
    render(<ResearchPanel />, { feature: { ActivityPanel } })

    await userEvent.click(screen.getByRole('button', { name: 'Open agent activity panel' }))

    expect(screen.getByText('Agent Activity')).toBeInTheDocument()
    expect(screen.getByText('Activity for job-1')).toBeInTheDocument()
  })

  test('a reopened session with no running job follows its last answer', async () => {
    useChatStore.setState({
      deepResearchJobId: null,
      currentConversation: {
        id: 'recorded',
        userId: 'local',
        title: 'Recorded',
        createdAt: new Date(),
        updatedAt: new Date(),
        messages: [
          { id: 'q', role: 'user', content: 'Which?', timestamp: new Date() },
          {
            id: 'a1',
            role: 'assistant',
            content: 'First',
            timestamp: new Date(),
            deepResearchJobId: 'job-first',
          },
          {
            id: 'a2',
            role: 'assistant',
            content: 'Second',
            timestamp: new Date(),
            deepResearchJobId: 'job-last',
          },
        ],
      },
    })
    render(<ResearchPanel />, { feature: { ActivityPanel } })

    await userEvent.click(screen.getByRole('button', { name: 'Open agent activity panel' }))

    expect(screen.getByText('Activity for job-last')).toBeInTheDocument()
  })

  test('resizes from the keyboard and resets to the default width', async () => {
    useLayoutStore.setState({ rightPanel: 'research' })
    render(<ResearchPanel />, { feature: { ActivityPanel } })
    const resizer = screen.getByRole('separator', { name: 'Resize agent activity panel' })
    const before = Number(resizer.getAttribute('aria-valuenow'))

    resizer.focus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(Number(resizer.getAttribute('aria-valuenow'))).toBe(before + 16)
    await userEvent.keyboard('{Enter}')
    expect(Number(resizer.getAttribute('aria-valuenow'))).toBe(before)
  })

  test('stops the running job', async () => {
    const cancelActiveDeepResearchJob = vi.fn().mockResolvedValue(undefined)
    useChatStore.setState({ isDeepResearchStreaming: true, cancelActiveDeepResearchJob })
    useLayoutStore.setState({ rightPanel: 'research' })
    render(<ResearchPanel />, { feature: { ActivityPanel } })

    await userEvent.click(screen.getByRole('button', { name: 'Stop run' }))

    expect(cancelActiveDeepResearchJob).toHaveBeenCalledOnce()
  })
})
