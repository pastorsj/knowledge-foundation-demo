// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { render, screen, within } from '@/test-utils'
import { useExecutionStore, type ExecutionRecord } from '../store'
import { readRecording } from '../test-utils/fixtures'
import { ActivityPanel } from './ActivityPanel'

const [turn] = (readRecording('sessions/returns-and-revenue.json') as { turns: ExecutionRecord[] })
  .turns

describe('ActivityPanel', () => {
  beforeEach(() => useExecutionStore.setState({ runs: {}, dropped: 0 }))
  afterEach(() => vi.restoreAllMocks())

  test('shows a recorded run as Thinking and Timeline, and nothing else', async () => {
    useExecutionStore.getState().addRecord(turn)
    render(<ActivityPanel jobId={turn.jobId} streaming={false} open />, {
      config: { mode: 'replay' },
    })

    const thinking = screen.getByRole('list', { name: 'Hermes thinking activity' })
    expect(within(thinking).getByText('Request accepted')).toBeVisible()
    expect(within(thinking).getByText('Table Query')).toBeVisible()
    expect(within(thinking).getByText('Document Retrieval')).toBeVisible()
    expect(within(thinking).getByText('Answer ready')).toBeVisible()
    expect(screen.getByText('Run complete')).toBeVisible()

    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))
    const timeline = screen.getByRole('region', { name: 'Execution action timeline' })
    const summary = within(timeline).getByLabelText('Timeline summary')
    expect(summary).toHaveTextContent('Tool calls2')
    expect(summary).toHaveTextContent('Token usage106,217')
    // The first action is selected, with its recorded result
    const details = within(timeline).getByTestId('timeline-action-details')
    expect(details).toHaveTextContent('Table Query')
    expect(details).toHaveTextContent('Structured result')
    expect(details).toHaveTextContent('Database: retail_sales')

    // The market demo's Benchmark tab is gone
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Thinking',
      'Timeline',
    ])
  })

  test('in live mode, shows a recorded session’s run without asking the API', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    useExecutionStore.getState().addRecord({ ...turn, recorded: true })
    render(<ActivityPanel jobId={turn.jobId} streaming={false} open />)

    expect(screen.getByText('Request accepted')).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))
    expect(screen.getByTestId('timeline-action-details')).toHaveTextContent('Structured result')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  test('without a job, each tab says what will appear', async () => {
    render(<ActivityPanel jobId={null} streaming={false} open />)

    expect(screen.getByText('No active run')).toBeVisible()
    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))
    expect(screen.getByText('No completed run')).toBeVisible()
  })

  test('loads the last answer of a reopened live session from its export', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => Response.json(turn))
    render(<ActivityPanel jobId={turn.jobId} streaming={false} open />)

    expect(await screen.findByText('Request accepted')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith(`/api/v1/jobs/async/job/${turn.jobId}/export`, {
      cache: 'no-store',
    })
  })

  test('after a streamed live run ends, loads the receipts its timeline shows', async () => {
    // The stream carried the events only
    useExecutionStore.getState().addRecord({ ...turn, receipts: [] })
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => Response.json(turn))
    render(<ActivityPanel jobId={turn.jobId} streaming={false} open />)

    await userEvent.click(screen.getByRole('tab', { name: 'Timeline' }))

    expect(await screen.findByText('Structured result')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledOnce()
  })

  test('says so when a live answer’s activity cannot be restored', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 404 }))
    render(<ActivityPanel jobId="gone" streaming={false} open />)

    expect(await screen.findByText('Activity unavailable')).toBeVisible()
  })

  test('a closed panel keeps its tabs but renders no view', () => {
    useExecutionStore.getState().addRecord(turn)
    render(<ActivityPanel jobId={turn.jobId} streaming={false} open={false} />, {
      config: { mode: 'replay' },
    })

    expect(screen.getByRole('tab', { name: 'Thinking' })).toBeInTheDocument()
    expect(screen.queryByText('Request accepted')).toBeNull()
  })
})
