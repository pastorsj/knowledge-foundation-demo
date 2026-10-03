// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen, waitFor } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useChatStore } from '@/features/chat'
import type { ExecutionWorkspaceProps, RecordingsSource } from '@/shared/context'
import { useLayoutStore } from '../store'
import { MainLayout } from './MainLayout'

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: vi.fn() }),
  usePathname: () => '/research',
  useSearchParams: () => new URLSearchParams(),
}))

const initialChat = useChatStore.getState()
const initialLayout = useLayoutStore.getState()

const SOURCES = [
  { id: 'market_analysis_structured', name: 'Market data' },
  { id: 'market_news', name: 'Market news' },
]

const recordings: RecordingsSource = {
  list: async () => [
    {
      id: 'rec-1',
      title: 'Market leaders',
      recordedAt: '2026-09-01T00:00:00Z',
      questions: ['Which assets led?'],
      tools: [],
    },
  ],
  load: async () => ({
    id: 'rec-1',
    title: 'Market leaders',
    recordedAt: '2026-09-01T00:00:00Z',
    turns: [
      {
        question: 'Which assets led?',
        answer: 'Asset A led.',
        jobId: 'job-1',
        sourceIds: ['market_analysis_structured'],
      },
    ],
  }),
}

describe('MainLayout', () => {
  beforeEach(() => {
    useChatStore.setState(initialChat, true)
    useChatStore.getState().setCurrentUser('local')
    useLayoutStore.setState({ ...initialLayout, availableDataSources: SOURCES }, true)
  })

  test('live mode offers the composer and data sources', () => {
    render(<MainLayout />)

    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add data sources' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /start new session/i })).toBeInTheDocument()
  })

  test('replay mode lists recordings and opens one read-only', async () => {
    render(<MainLayout />, { config: { mode: 'replay' }, feature: { recordings } })

    // As the original demo UI shows a recorded session: read only, not hidden
    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toBeDisabled()
    expect(screen.getByText('Recorded test session · read only')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add data sources' })).toBeDisabled()
    expect(await screen.findByText('Showing 1 of 1 sessions · 1 of 1 questions')).toBeVisible()
    expect(screen.getByRole('tab', { name: /Recorded \(1\)/ })).toHaveAttribute(
      'aria-selected',
      'true'
    )

    await userEvent.click(
      screen.getByRole('button', { name: 'Recorded session: Market leaders; Completed' })
    )

    expect(await screen.findByText('Asset A led.')).toBeInTheDocument()
    expect(useChatStore.getState().currentConversation?.readOnly).toBe(true)
    // Its data sources show in the composer's counter
    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(['market_analysis_structured'])
    // Recorded sessions are shown, never saved.
    expect(useChatStore.getState().conversations).toEqual([])
  })

  test('live mode lists the recordings beside My sessions, and opens one read-only', async () => {
    render(<MainLayout />, { feature: { recordings } })

    // As in the original demo UI: My sessions first, the recordings one tab away
    const recordedTab = await screen.findByRole('tab', { name: /Recorded \(1\)/ })
    expect(screen.getByRole('tab', { name: 'My sessions' })).toHaveAttribute(
      'aria-selected',
      'true'
    )
    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toBeEnabled()

    await userEvent.click(recordedTab)
    await userEvent.click(
      screen.getByRole('button', { name: 'Recorded session: Market leaders; Completed' })
    )

    expect(await screen.findByText('Asset A led.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toBeDisabled()
    expect(screen.getByText('Recorded test session · read only')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add data sources' })).toBeDisabled()
    expect(useChatStore.getState().conversations).toEqual([])
  })

  test('live mode without recordings shows the sessions alone', async () => {
    const missing: RecordingsSource = {
      list: async () => {
        throw new Error('/api/recordings/index.json returned 404')
      },
      load: recordings.load,
    }
    render(<MainLayout />, { feature: { recordings: missing } })

    await waitFor(() => expect(screen.getByText('No sessions yet')).toBeInTheDocument())
    expect(screen.queryByRole('tab', { name: /Recorded/ })).not.toBeInTheDocument()
  })

  test('the composer offers the pack questions as demo scenarios', async () => {
    render(
      <MainLayout
        demoScenarios={[
          {
            id: 'market-leaders',
            label: 'Market Leaders',
            tools: ['cudf'],
            description: 'Scan the most liquid issuers.',
            question: 'Which assets led?',
            sourceIds: ['market_news'],
          },
        ]}
      />
    )

    expect(screen.getByTestId('demo-scenario-control')).toHaveTextContent('Demo scenario')
    await userEvent.click(screen.getByTestId('demo-scenario-select'))
    const option = await screen.findByRole('option', { name: /Market Leaders/ })
    // The tools it is expected to use, as pills in place of the old type tag
    expect(option.querySelector('.tool-pill')).toHaveTextContent('cuDF')
    await userEvent.click(option)

    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toHaveValue(
      'Which assets led?'
    )
    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(['market_news'])
  })

  test('places a featured question and its data sources in a new session', async () => {
    render(
      <MainLayout initialQuestion={{ question: 'Which assets led?', sourceIds: ['market_news'] }} />
    )

    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Chat message input' })).toHaveValue(
        'Which assets led?'
      )
    )
    expect(useLayoutStore.getState().enabledDataSourceIds).toEqual(['market_news'])
  })

  test('shows the execution workspace for an opened run', async () => {
    const Workspace = ({ jobId, onClose }: ExecutionWorkspaceProps) => (
      <button onClick={onClose}>Workspace for {jobId}</button>
    )
    useLayoutStore.getState().openExecution('job-1')
    render(<MainLayout />, { feature: { Workspace } })

    await userEvent.click(screen.getByRole('button', { name: 'Workspace for job-1' }))

    expect(useLayoutStore.getState().execution).toBeNull()
    expect(screen.queryByText('Workspace for job-1')).not.toBeInTheDocument()
  })

  test('gives the workspace the question and data sources that started the run', async () => {
    const Workspace = ({ question, sourceIds }: ExecutionWorkspaceProps) => (
      <p>
        {question} from {sourceIds?.join(', ')}
      </p>
    )
    render(<MainLayout />, { config: { mode: 'replay' }, feature: { recordings, Workspace } })
    await userEvent.click(
      await screen.findByRole('button', { name: 'Recorded session: Market leaders; Completed' })
    )
    await screen.findByText('Asset A led.')

    useLayoutStore.getState().openExecution('job-1')
    expect(
      await screen.findByText('Which assets led? from market_analysis_structured')
    ).toBeInTheDocument()
  })
})
