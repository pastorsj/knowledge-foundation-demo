// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { beforeEach, describe, expect, it } from 'vitest'
import { executionFeature } from './index'
import { useExecutionStore } from './store'
import { fixtureEvents, fixtureReceipts } from './test-utils/fixtures'

const JOB = fixtureEvents[0].jobId

describe('execution store', () => {
  beforeEach(() => useExecutionStore.setState({ runs: {}, dropped: 0 }))

  it('keeps each event once when the stream replays from the start', () => {
    const { addEvent } = useExecutionStore.getState()
    for (const event of [...fixtureEvents, ...fixtureEvents]) addEvent(JOB, event)
    expect(useExecutionStore.getState().runs[JOB].events).toEqual(fixtureEvents)
  })

  it('merges a recorded or exported turn into the live events', () => {
    const { addEvent, addRecord } = useExecutionStore.getState()
    addEvent(JOB, fixtureEvents[0])
    addRecord({ jobId: JOB, events: fixtureEvents, receipts: fixtureReceipts.slice(0, 2) })
    const run = useExecutionStore.getState().runs[JOB]
    expect(run.events).toHaveLength(fixtureEvents.length)
    expect(Object.keys(run.receipts)).toEqual(fixtureReceipts.slice(0, 2).map((r) => r.receiptId))
  })

  it('counts and drops records that fail the contract', () => {
    const { addEvent, addRecord } = useExecutionStore.getState()
    addEvent(JOB, { schemaVersion: '1' })
    addRecord({ jobId: JOB, events: ['bad'], receipts: [{}] })
    expect(useExecutionStore.getState().dropped).toBe(3)
    expect(useExecutionStore.getState().runs[JOB].events).toEqual([])
  })

  it('keeps the job status of an exported or recorded turn', () => {
    useExecutionStore
      .getState()
      .addRecord({ jobId: JOB, events: [], receipts: [], status: 'failure' })
    expect(useExecutionStore.getState().runs[JOB].jobStatus).toBe('failure')
  })

  it('takes execution.v2 events and job.status frames from the job stream', () => {
    const { cursor: _cursor, ...frame } = fixtureEvents[0]
    const { onJobEvent } = executionFeature
    onJobEvent({ jobId: JOB, cursor: '1', type: 'execution.v2', data: frame })
    onJobEvent({ jobId: JOB, cursor: null, type: 'job.status', data: { status: 'running' } })
    onJobEvent({
      jobId: JOB,
      cursor: null,
      type: 'job.status',
      data: { data: { status: 'interrupted' } },
    })
    onJobEvent({ jobId: JOB, cursor: null, type: 'artifact.update', data: { status: 'success' } })
    const run = useExecutionStore.getState().runs[JOB]
    expect(run.events).toEqual([fixtureEvents[0]])
    expect(run.jobStatus).toBe('interrupted')
  })
})
