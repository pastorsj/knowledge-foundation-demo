// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { ReactNode } from 'react'
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { useLayoutStore } from '@/features/layout/store'
import { ExecutionFeatureProvider, noExecutionFeature } from '@/shared/context'
import { FakeEventSource } from '@/test-utils/fake-event-source'
import { useChatStore } from '../store'
import { useDeepResearch } from './use-deep-research'

const initialChat = useChatStore.getState()
const initialLayout = useLayoutStore.getState()
const chat = () => useChatStore.getState()

const onJobEvent = vi.fn()
const wrapper = ({ children }: { children: ReactNode }) => (
  <ExecutionFeatureProvider feature={{ ...noExecutionFeature, onJobEvent }}>
    {children}
  </ExecutionFeatureProvider>
)

/** Start following job-1 and return its stream once connected. */
const followJob = (): FakeEventSource => {
  chat().ensureSession()
  chat().addUserMessage('Which stores led in Q3?')
  const messageId = chat().addAgentResponseWithMeta('', {
    deepResearchJobId: 'job-1',
    deepResearchJobStatus: 'submitted',
    isDeepResearchActive: true,
  })
  chat().startDeepResearch('job-1', messageId)
  renderHook(() => useDeepResearch(), { wrapper })
  act(() => {
    vi.advanceTimersByTime(50)
  })
  return FakeEventSource.latest
}

describe('useDeepResearch', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
    useChatStore.setState(initialChat, true)
    useLayoutStore.setState(initialLayout, true)
    chat().setCurrentUser('local')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  test('does not connect without a running job', () => {
    renderHook(() => useDeepResearch(), { wrapper })
    act(() => {
      vi.advanceTimersByTime(50)
    })

    expect(FakeEventSource.instances).toEqual([])
  })

  test('forwards every record to the execution view', () => {
    const stream = followJob()

    act(() => stream.emit('execution.v2', { eventKind: 'run.started' }, '1'))

    expect(stream.url).toBe('/api/v1/jobs/async/job/job-1/stream')
    expect(onJobEvent).toHaveBeenCalledWith({
      jobId: 'job-1',
      cursor: '1',
      type: 'execution.v2',
      data: { eventKind: 'run.started' },
    })
  })

  test('settles the answer when the job succeeds', () => {
    const stream = followJob()

    act(() => {
      stream.emit('job.status', { status: 'running' })
      stream.emit('artifact.update', {
        data: { type: 'output', output_category: 'final_report', content: '# Answer' },
      })
      stream.emit('job.status', { status: 'success' })
    })

    const answer = chat().currentConversation?.messages.at(-1)
    expect(answer).toMatchObject({ content: '# Answer', deepResearchJobStatus: 'success' })
    expect(chat().isDeepResearchStreaming).toBe(false)
  })

  test('fails the run when the stream cannot be recovered', () => {
    const stream = followJob()

    act(() => stream.fail(FakeEventSource.CLOSED))

    expect(chat().deepResearchStatus).toBe('failure')
    expect(chat().currentConversation?.messages.at(-1)?.errorData?.errorMessage).toMatch(
      /Lost connection to the run/
    )
  })
})
