// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { ApiRequestError, cancelJob, getJobStatus, submitJob } from '@/adapters/api'
import { useLayoutStore } from '@/features/layout/store'
import { useChatStore } from '../store'
import { useHermesChat } from './use-hermes-chat'

vi.mock('@/adapters/api', async (importOriginal) => ({
  ApiRequestError: (await importOriginal<typeof import('@/adapters/api')>()).ApiRequestError,
  cancelJob: vi.fn(),
  getJobReport: vi.fn(),
  getJobStatus: vi.fn(),
  submitJob: vi.fn(),
}))

const initialChat = useChatStore.getState()
const initialLayout = useLayoutStore.getState()
const chat = () => useChatStore.getState()

const send = (question: string) => {
  const { result } = renderHook(() => useHermesChat())
  act(() => result.current.sendMessage(question))
  return result
}

describe('useHermesChat', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    useChatStore.setState(initialChat, true)
    useLayoutStore.setState({ ...initialLayout, enabledDataSourceIds: ['retail.policies'] }, true)
    chat().setCurrentUser('local')
    chat().ensureSession()
  })

  test('submits the question as a job and follows it once admitted', async () => {
    vi.mocked(submitJob).mockImplementation(async ({ jobId }) => ({
      job_id: jobId,
      status: 'submitted',
    }))

    send('  Which assets led?  ')

    const request = vi.mocked(submitJob).mock.calls[0][0]
    expect(request).toMatchObject({
      input: 'Which assets led?',
      conversationId: chat().currentConversation!.id,
      dataSources: ['retail.policies'],
    })
    // The answer placeholder carries the job ID before the request returns.
    const placeholder = chat().currentConversation!.messages[1]
    expect(placeholder).toMatchObject({
      messageType: 'agent_response',
      deepResearchJobId: request.jobId,
      isDeepResearchActive: true,
    })
    await waitFor(() => expect(chat().deepResearchJobId).toBe(request.jobId))
    expect(chat().isDeepResearchStreaming).toBe(true)
  })

  test('reports a rejected submission', async () => {
    vi.mocked(submitJob).mockRejectedValue(new Error('Failed to start research: 422 - bad source'))

    send('Which assets led?')

    await waitFor(() =>
      expect(chat().currentConversation?.messages.at(-1)?.errorData?.errorMessage).toBe(
        'Failed to start research: 422 - bad source'
      )
    )
    expect(chat().isStreaming).toBe(false)
    // As in the original demo UI, the error card alone reports it: no job, so no run banner.
    expect(chat().currentConversation?.messages.map((m) => m.messageType)).toEqual([
      'user',
      'agent_response',
      'error',
    ])
  })

  test('recovers through the job status when the API was unreachable', async () => {
    vi.mocked(submitJob).mockRejectedValue(new Error('Failed to start research: 502'))
    vi.mocked(getJobStatus).mockResolvedValue({ job_id: 'x', status: 'running', error: null })

    send('Which assets led?')

    await waitFor(() => expect(chat().isDeepResearchStreaming).toBe(true))
    expect(chat().currentConversation?.messages.some((m) => m.messageType === 'error')).toBe(false)
  })

  test('reports a submission the API refused while starting, instead of following a job it never created', async () => {
    const message =
      'Failed to start research: 503 - The API is starting or stopping. Try again shortly.'
    vi.mocked(submitJob).mockRejectedValue(new ApiRequestError(message, 503, true))

    send('Which assets led?')

    await waitFor(() =>
      expect(chat().currentConversation?.messages.at(-1)?.errorData?.errorMessage).toBe(message)
    )
    expect(getJobStatus).not.toHaveBeenCalled()
    expect(chat().isStreaming).toBe(false)
  })

  test('recovers through the job status when the UI proxy could not reach the API', async () => {
    vi.mocked(submitJob).mockRejectedValue(
      new ApiRequestError('Failed to start research: 502 - The API is unavailable', 502, false)
    )
    vi.mocked(getJobStatus).mockResolvedValue({ job_id: 'x', status: 'running', error: null })

    send('Which assets led?')

    await waitFor(() => expect(chat().isDeepResearchStreaming).toBe(true))
    expect(chat().currentConversation?.messages.some((m) => m.messageType === 'error')).toBe(false)
  })

  test('stop cancels the running job', async () => {
    vi.mocked(cancelJob).mockResolvedValue({ cancelled: true })
    useChatStore.setState({ deepResearchJobId: 'job-1', isDeepResearchStreaming: true })
    const { result } = renderHook(() => useHermesChat())

    act(() => result.current.stop())

    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith('job-1'))
  })
})
