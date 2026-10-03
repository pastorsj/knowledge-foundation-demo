// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for session-activity utility functions
 */

import { describe, it, expect } from 'vitest'
import {
  hasActiveDeepResearchJob,
  hasCompletedDeepResearchReport,
  lastUserMessageTime,
} from './session-activity'
import type { ChatMessage } from '../types'

/**
 * Helper to create a minimal ChatMessage for testing
 */
const makeMessage = (overrides: Partial<ChatMessage> = {}): ChatMessage => ({
  id: 'msg-1',
  role: 'assistant',
  content: '',
  timestamp: new Date(),
  ...overrides,
})

describe('lastUserMessageTime', () => {
  it('honors a role-only user message with no messageType', () => {
    const when = new Date('2026-05-01T12:00:00Z')
    expect(
      lastUserMessageTime([makeMessage({ role: 'user', content: 'hi', timestamp: when })])
    ).toBe(when.getTime())
  })

  it('returns the most recent user message time, skipping non-user messages', () => {
    const when = new Date('2026-05-02T09:00:00Z')
    expect(
      lastUserMessageTime([
        makeMessage({ role: 'user', content: 'earlier', timestamp: new Date('2026-05-01T00:00:00Z') }),
        makeMessage({ role: 'user', content: 'latest', timestamp: when }),
        makeMessage({ messageType: 'agent_response', timestamp: new Date('2026-05-03T00:00:00Z') }),
      ])
    ).toBe(when.getTime())
  })

  it('returns null when only assistant/system messages exist', () => {
    expect(lastUserMessageTime([makeMessage({ role: 'assistant' })])).toBeNull()
  })
})

describe('hasActiveDeepResearchJob', () => {
  it('returns false for empty message array', () => {
    expect(hasActiveDeepResearchJob([])).toBe(false)
  })

  it('returns false when no messages have deep research job IDs', () => {
    const messages = [
      makeMessage({ messageType: 'user' }),
      makeMessage({ messageType: 'agent_response' }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(false)
  })

  it('returns true when most recent job status is "submitted"', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'submitted',
      }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(true)
  })

  it('returns true when most recent job status is "running"', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'running',
      }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(true)
  })

  it('returns false when most recent job status is "success"', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'success',
      }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(false)
  })

  it('returns false when most recent job status is "failure"', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'failure',
      }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(false)
  })

  it('returns false when most recent job status is "interrupted"', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'interrupted',
      }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(false)
  })

  it('checks MOST RECENT job message, not first', () => {
    const messages = [
      makeMessage({
        id: 'msg-old',
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'running',
      }),
      makeMessage({ id: 'msg-middle', messageType: 'user' }),
      makeMessage({
        id: 'msg-new',
        messageType: 'agent_response',
        deepResearchJobId: 'job-2',
        deepResearchJobStatus: 'success',
      }),
    ]
    // Most recent job (job-2) is success, so not active
    expect(hasActiveDeepResearchJob(messages)).toBe(false)
  })

  it('returns true when most recent job is running even if older jobs are complete', () => {
    const messages = [
      makeMessage({
        id: 'msg-old',
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'success',
      }),
      makeMessage({ id: 'msg-user', messageType: 'user' }),
      makeMessage({
        id: 'msg-new',
        messageType: 'agent_response',
        deepResearchJobId: 'job-2',
        deepResearchJobStatus: 'running',
      }),
    ]
    expect(hasActiveDeepResearchJob(messages)).toBe(true)
  })

  it('ignores messages without deepResearchJobId', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'running',
      }),
      // Non-DR agent_response at the end — should be skipped
      makeMessage({
        id: 'msg-latest',
        messageType: 'agent_response',
        content: 'Just a regular response',
      }),
    ]
    // The latest agent_response with a job ID is job-1 (running)
    expect(hasActiveDeepResearchJob(messages)).toBe(true)
  })
})

describe('hasCompletedDeepResearchReport', () => {
  it('returns true when the latest job succeeded with an answer', () => {
    const messages = [
      makeMessage({
        messageType: 'agent_response',
        content: 'The answer',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'success',
      }),
    ]

    expect(hasCompletedDeepResearchReport(messages)).toBe(true)
  })

  it('returns false when the latest job ended without an answer', () => {
    const messages = [
      makeMessage({
        id: 'old-job',
        messageType: 'agent_response',
        content: 'Earlier answer',
        deepResearchJobId: 'job-1',
        deepResearchJobStatus: 'success',
      }),
      makeMessage({
        id: 'latest-job',
        messageType: 'agent_response',
        deepResearchJobId: 'job-2',
        deepResearchJobStatus: 'failure',
      }),
    ]

    expect(hasCompletedDeepResearchReport(messages)).toBe(false)
  })
})
