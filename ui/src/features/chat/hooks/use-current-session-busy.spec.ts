// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for useIsCurrentSessionBusy hook
 *
 * Tests cover three categories:
 * 1. Ephemeral state (normal operation — isStreaming, SSE, deepResearchStatus)
 * 2. Persisted state (page refresh recovery — message history)
 * 3. Combined state (ephemeral + persisted together)
 */

import { renderHook } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { useIsCurrentSessionBusy } from './use-current-session-busy'
import { useChatStore } from '../store'

// Mock the chat store
vi.mock('../store', () => ({
  useChatStore: vi.fn(),
}))

// Mock the session-activity utility (pure functions tested separately)
vi.mock('../lib/session-activity', () => ({
  hasActiveDeepResearchJob: vi.fn(() => false),
}))

import { hasActiveDeepResearchJob } from '../lib/session-activity'
const mockHasActiveJob = hasActiveDeepResearchJob as unknown as ReturnType<typeof vi.fn>

/**
 * Default idle state — all flags off, no persisted activity.
 */
const idleState = {
  isStreaming: false,
  isDeepResearchStreaming: false,
  deepResearchStatus: null,
  deepResearchOwnerConversationId: null,
  currentConversation: { id: 'conv-1', messages: [] },
}

describe('useIsCurrentSessionBusy', () => {
  const mockUseChatStore = useChatStore as unknown as ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.clearAllMocks()
    mockHasActiveJob.mockReturnValue(false)
  })

  // ─── Ephemeral State Tests ─────────────────────────────────────

  it('returns false when no operations are active', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector(idleState)
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(false)
  })

  it('returns true while a question is being submitted', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({ ...idleState, isStreaming: true })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(true)
  })

  it('returns true when deep research SSE is streaming and owned by the current session', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({
        ...idleState,
        isDeepResearchStreaming: true,
        deepResearchStatus: 'running',
        deepResearchOwnerConversationId: 'conv-1',
      })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(true)
  })

  it('returns true when deep research status is "submitted" for the current session', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({
        ...idleState,
        deepResearchStatus: 'submitted',
        deepResearchOwnerConversationId: 'conv-1',
      })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(true)
  })

  it('returns true when deep research status is "running" for the current session', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({
        ...idleState,
        deepResearchStatus: 'running',
        deepResearchOwnerConversationId: 'conv-1',
      })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(true)
  })

  it('returns false when deep research is streaming but owned by another session', () => {
    // Session A owns the background deep research; session B (current) must not be
    // marked busy, so no Stop button renders and B's socket cannot be disconnected.
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({
        ...idleState,
        isDeepResearchStreaming: true,
        deepResearchStatus: 'running',
        deepResearchOwnerConversationId: 'conv-A',
        currentConversation: { id: 'conv-B', messages: [] },
      })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(false)
  })

  it('returns false when deep research status is "success" (terminal state)', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({ ...idleState, deepResearchStatus: 'success' })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(false)
  })

  it('returns false when deep research status is "failure" (terminal state)', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({ ...idleState, deepResearchStatus: 'failure' })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(false)
  })

  it('returns false when deep research status is "interrupted" (terminal state)', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({ ...idleState, deepResearchStatus: 'interrupted' })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(false)
  })

  it('returns true when a submission and a job are both active', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({
        ...idleState,
        isStreaming: true,
        isDeepResearchStreaming: true,
        deepResearchStatus: 'running',
        deepResearchOwnerConversationId: 'conv-1',
      })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(true)
  })

  // ─── Persisted State Tests (Page Refresh Recovery) ─────────────

  it('returns true when message history has active deep research job (refresh scenario)', () => {
    // Simulate page refresh: ephemeral state is reset, but persisted messages indicate active job
    mockHasActiveJob.mockReturnValue(true)

    mockUseChatStore.mockImplementation(
      (selector: (state: object) => unknown) => selector(idleState) // All ephemeral state is idle
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(true)
  })

  it('returns false when currentConversation is null', () => {
    mockUseChatStore.mockImplementation((selector: (state: object) => unknown) =>
      selector({ ...idleState, currentConversation: null })
    )

    const { result } = renderHook(() => useIsCurrentSessionBusy())
    expect(result.current).toBe(false)
  })
})
