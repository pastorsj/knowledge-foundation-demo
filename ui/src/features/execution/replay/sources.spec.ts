// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useExecutionStore } from '../store'
import { readRecording } from '../test-utils/fixtures'
import { archiveId, loadJobExport, parseIndex, parseSession, recordings } from './sources'

const index = readRecording('index.json')
const session = readRecording('sessions/returns-and-revenue.json')

/** Serves the committed e2e bundle the way /api/recordings does. */
const serveBundle = () =>
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const path = String(input).replace('/api/recordings/retail/', '')
    return path.startsWith('/')
      ? new Response(null, { status: 404 })
      : Response.json(readRecording(path))
  })

describe('recordings bundle (v2)', () => {
  beforeEach(() => useExecutionStore.setState({ runs: {}, dropped: 0 }))
  afterEach(() => vi.restoreAllMocks())

  it('parses the committed e2e bundle', () => {
    expect(parseIndex(index).sessions.map((s) => s.id)).toEqual([
      'returns-and-revenue',
      'gold-tier-and-churn',
    ])
    expect(parseSession(session).turns[0].events).toHaveLength(13)
  })

  it('asks for a re-record when the bundle is not v2', () => {
    expect(() => parseIndex({ formatVersion: 2 })).toThrow(/re-record/)
    expect(() => parseSession({ schemaVersion: 1, turns: [] })).toThrow(/re-record/)
  })

  it('lists the sessions and loads one into the chat and the execution store', async () => {
    serveBundle()
    expect(await recordings.list('retail')).toEqual([
      {
        id: 'returns-and-revenue',
        title: 'Returns policy and loyalty revenue',
        recordedAt: '2026-09-28T06:00:00Z',
        questions: [
          'What is the return window for opened electronics, and which loyalty tier brought the most net revenue in the third quarter of 2026?',
        ],
        tools: [
          { pill: 'retrieval', tools: ['retrieve_evidence'] },
          { pill: 'duckdb', tools: ['query_tables'] },
        ],
      },
      {
        id: 'gold-tier-and-churn',
        title: 'Gold-tier orders and churn risk',
        recordedAt: '2026-09-28T06:00:00Z',
        questions: [
          'How many orders did gold-tier customers place, and what was their average order value?',
          'Which customers are most likely to stop ordering in the next 90 days?',
        ],
        // Not in this index: the recordings route derives them (app/api/recordings)
        tools: [],
      },
    ])

    const loaded = await recordings.load('retail', 'gold-tier-and-churn')
    expect(loaded.turns.map((turn) => turn.jobId)).toEqual([
      '0dcd9841-b68a-456b-9dc5-87403c34efcb',
      '18d6824d-df8e-4d57-89eb-22a055226124',
    ])
    expect(loaded.turns[0]).toMatchObject({
      sourceIds: ['retail.sales'],
      answer: expect.stringContaining('**References:**'),
    })
    const runs = useExecutionStore.getState().runs
    expect(runs['0dcd9841-b68a-456b-9dc5-87403c34efcb'].events).toHaveLength(8)
    expect(Object.keys(runs['18d6824d-df8e-4d57-89eb-22a055226124'].receipts)).toHaveLength(1)
    // Recorded runs have no live job behind them, in either mode
    expect(runs['0dcd9841-b68a-456b-9dc5-87403c34efcb'].recorded).toBe(true)
    // …and the archive they came from, as the original keyed recorded runs
    expect(runs['0dcd9841-b68a-456b-9dc5-87403c34efcb'].archive).toBe('20260928T060000Z-retail')
    expect(useExecutionStore.getState().dropped).toBe(0)
  })

  it('names a bundle’s archive by when it was recorded and its pack', () => {
    expect(archiveId(parseIndex(index))).toBe('20260928T060000Z-retail')
    expect(
      archiveId({
        ...parseIndex(index),
        recordedAt: '2026-10-01T04:55:12.689096Z',
        pack: { id: 'manufacturing', version: '0.1.0' },
      })
    ).toBe('20261001T045512Z-manufacturing')
    expect(archiveId({ ...parseIndex(index), recordedAt: '2026-10-01T04:55:12+00:00' })).toBe(
      '20261001T045512Z-retail'
    )
  })

  it('loads a recorded session without its archive when the index is unavailable', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
      String(input).endsWith('index.json')
        ? new Response(null, { status: 503 })
        : Response.json(readRecording('sessions/gold-tier-and-churn.json'))
    )
    await recordings.load('retail', 'gold-tier-and-churn')
    const run = useExecutionStore.getState().runs['0dcd9841-b68a-456b-9dc5-87403c34efcb']
    expect(run.recorded).toBe(true)
    expect(run.archive).toBeNull()
  })

  it('loads a live job from its export', async () => {
    const turn = (session as { turns: unknown[] }).turns[0]
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json(turn))
    await loadJobExport('2e1e9c6d-8c4a-4d2f-9716-1cb1e6659c78')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/jobs/async/job/2e1e9c6d-8c4a-4d2f-9716-1cb1e6659c78/export',
      { cache: 'no-store' }
    )
    const run = useExecutionStore.getState().runs['2e1e9c6d-8c4a-4d2f-9716-1cb1e6659c78']
    expect(Object.keys(run.receipts)).toHaveLength(2)
  })

  it('fails when the export is missing', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 404 }))
    await expect(loadJobExport('gone')).rejects.toThrow(/404/)
  })
})
