// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { selectActivityJobId } from './selectors'
import type { Conversation } from './types'

const conversation = (jobIds: Array<string | undefined>): Conversation => ({
  id: 'c',
  userId: 'local',
  title: 'c',
  createdAt: new Date(),
  updatedAt: new Date(),
  messages: jobIds.map((jobId, index) => ({
    id: `m${index}`,
    role: 'assistant',
    content: '',
    timestamp: new Date(),
    deepResearchJobId: jobId,
  })),
})

describe('selectActivityJobId', () => {
  test('follows the running job, else the last answer of the conversation', () => {
    const current = conversation(['job-1', 'job-2', undefined])
    expect(
      selectActivityJobId({ deepResearchJobId: 'job-live', currentConversation: current })
    ).toBe('job-live')
    expect(selectActivityJobId({ deepResearchJobId: null, currentConversation: current })).toBe(
      'job-2'
    )
    expect(selectActivityJobId({ deepResearchJobId: null, currentConversation: null })).toBeNull()
  })
})
