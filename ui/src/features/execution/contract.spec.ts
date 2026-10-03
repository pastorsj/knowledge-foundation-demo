// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import { toExecutionEvent, toReceipt } from './contract'
import { fixtureEvents, fixtureReceipts } from './test-utils/fixtures'

describe('execution contract guard', () => {
  it('accepts every golden event and receipt unchanged', () => {
    for (const event of fixtureEvents) expect(toExecutionEvent(event)).toEqual(event)
    for (const receipt of fixtureReceipts) expect(toReceipt(receipt)).toEqual(receipt)
  })

  it('takes the cursor from the SSE id when the frame data has none', () => {
    const { cursor: _cursor, ...frame } = fixtureEvents[0]
    expect(toExecutionEvent(frame, '7')?.cursor).toBe(7)
    expect(toExecutionEvent(frame)?.cursor).toBeNull()
  })

  it.each([
    ['a v1 event', { ...fixtureEvents[0], schemaVersion: '1' }],
    ['an unknown state', { ...fixtureEvents[0], state: 'paused' }],
    ['no display', { ...fixtureEvents[0], display: null }],
    ['a string', 'execution.v2'],
  ])('drops %s', (_name, value) => {
    expect(toExecutionEvent(value)).toBeNull()
  })

  it.each([
    ['an unknown artifact kind', { ...fixtureReceipts[0], artifactKind: 'benchmark' }],
    ['a pending status', { ...fixtureReceipts[0], status: 'pending' }],
    ['list content', { ...fixtureReceipts[0], content: [] }],
  ])('drops a receipt with %s', (_name, value) => {
    expect(toReceipt(value)).toBeNull()
  })
})
