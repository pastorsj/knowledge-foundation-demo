// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  budgetFor,
  checkCitations,
  checkClosing,
  checkLatency,
  checkPills,
  checkReplay,
  checkSuccess,
  defaultBudget,
  failed,
  formatTable,
  HttpStatusError,
  parseBudgets,
  parseStream,
  recordedSeconds,
  withRetries,
  selectQuestions,
  type LiveEvent,
  type LiveTurn,
  type QuestionResult,
} from './checks'

const closing = (profile = 'enterprise-research'): LiveEvent[] => [
  { eventKind: 'run.completed', state: 'completed' },
  { eventKind: 'report.completed', state: 'completed' },
  { eventKind: 'report.reference_resolution', state: 'completed' },
  {
    eventKind: 'report.metrics',
    state: 'completed',
    display: { attributes: { runtime_profile: profile, wall_duration_ms: 32_500 } },
  },
]

/** A successful anomaly-scan run on the GPU, cited, ending on the closing events. */
const turn = (overrides: Partial<LiveTurn> = {}): LiveTurn => ({
  jobId: 'job-1',
  status: 'success',
  report: { markdown: 'Unusual [1].', citations: [{ evidenceId: 'r1' }] },
  events: [
    { eventKind: 'run.created', state: 'started' },
    { eventKind: 'tool.started', state: 'started', toolName: 'skill_view' },
    {
      eventKind: 'artifact.available',
      state: 'completed',
      toolName: 'market_anomaly_scan',
      artifactRefs: ['r1'],
    },
    ...closing(),
    { eventKind: 'run.heartbeat', state: 'progress' },
  ],
  receipts: [
    {
      receiptId: 'r1',
      status: 'completed',
      content: { engine: { device: 'gpu', library: 'cuml.accel' } },
    },
  ],
  ...overrides,
})

const QUESTIONS = [
  {
    id: 'market-leaders',
    question: 'Who led?',
    sources: ['market_data'],
    tools: ['cudf'],
    featured: true,
  },
  { id: 'sector-sql', question: 'Sectors?', sources: ['market_data'], tools: ['ontology'] },
  {
    id: 'peer-network',
    question: 'Peers?',
    sources: ['market_data'],
    tools: ['cudf'],
    featured: true,
  },
]

describe('questions and budgets', () => {
  it('runs the featured questions by default, or the requested ones in the order asked', () => {
    expect(selectQuestions(QUESTIONS, undefined).map((q) => q.id)).toEqual([
      'market-leaders',
      'peer-network',
    ])
    expect(selectQuestions(QUESTIONS, 'sector-sql, market-leaders').map((q) => q.id)).toEqual([
      'sector-sql',
      'market-leaders',
    ])
    expect(() => selectQuestions(QUESTIONS, 'nope')).toThrow(
      /no question nope; it offers market-leaders/
    )
  })

  it('takes one budget for all and budgets by question, and refuses anything else', () => {
    const budgets = parseBudgets('300, market-leaders=60')
    expect(budgets).toEqual({ all: 300, byQuestion: { 'market-leaders': 60 } })
    expect(budgetFor('market-leaders', budgets, 10)).toBe(60)
    expect(budgetFor('peer-network', budgets, 10)).toBe(300)
    expect(() => parseBudgets('fast')).toThrow(/not a budget: fast/)
    expect(() => parseBudgets('=30')).toThrow(/not a budget/)
    expect(() => parseBudgets('a=-1')).toThrow(/not a budget/)
  })

  it('defaults to three times the recorded run, between two and ten minutes', () => {
    expect(defaultBudget(33)).toBe(120)
    expect(defaultBudget(89)).toBe(270)
    expect(defaultBudget(138)).toBe(420)
    expect(defaultBudget(400)).toBe(600)
    expect(defaultBudget(null)).toBe(300)
    expect(budgetFor('x', parseBudgets(''), 89)).toBe(270)
  })

  it('reads the recorded duration of a session, and of each committed featured recording', () => {
    expect(recordedSeconds({ turns: [turn()] })).toBe(32.5)
    expect(recordedSeconds({ turns: [] })).toBeNull()
    const sessions = path.resolve(
      process.cwd(),
      '..',
      'data',
      'packs',
      'synthetic-market',
      'recordings'
    )
    const index = JSON.parse(readFileSync(path.join(sessions, 'index.json'), 'utf8')) as {
      sessions: Array<{ id: string; featured: boolean }>
    }
    for (const session of index.sessions.filter((s) => s.featured)) {
      const file = path.join(sessions, 'sessions', `${session.id}.json`)
      expect(recordedSeconds(JSON.parse(readFileSync(file, 'utf8'))), session.id).toBeGreaterThan(0)
    }
  })
})

describe('the checks of one run', () => {
  it('passes a good run', () => {
    expect(checkSuccess('success', turn()).ok).toBe(true)
    expect(checkCitations(turn())).toEqual({ ok: true, detail: '1 cited' })
    expect(checkPills(['cudf', 'cuml'], ['cuml', 'cudf'], turn())).toEqual({
      ok: true,
      detail: 'cuDF cuML',
    })
    expect(checkClosing(turn())).toEqual({ ok: true, detail: 'enterprise-research' })
    expect(checkLatency(42.4, 120)).toEqual({ ok: true, detail: '42 s of 120' })
  })

  it('fails a failed or stalled job', () => {
    expect(checkSuccess('failure', turn({ status: 'failure' }))).toEqual({
      ok: false,
      detail: 'job failure',
    })
    expect(checkSuccess('stalled', turn({ status: 'running' })).detail).toBe(
      'job stalled, export running'
    )
    expect(checkSuccess('success', null).ok).toBe(false)
  })

  it('needs a citation that resolves to a completed receipt', () => {
    expect(checkCitations(turn({ report: { citations: [] } })).detail).toBe('no citations')
    const stray = turn({ report: { citations: [{ evidenceId: 'r9' }] } })
    expect(checkCitations(stray).detail).toBe('no citation resolves to a receipt')
    const partial = turn({ report: { citations: [{ evidenceId: 'r1' }, { evidenceId: 'r9' }] } })
    expect(checkCitations(partial)).toEqual({ ok: false, detail: '1 citation(s) unresolved' })
  })

  it('compares the picker with the declared pills, and the declared pills with the tools the run used', () => {
    expect(checkPills(['cudf'], ['cudf', 'cuml'], turn()).detail).toBe(
      'picker shows cudf cuml, declared cudf'
    )
    expect(checkPills(['cudf', 'cugraph'], null, turn())).toEqual({
      ok: false,
      detail: 'declared cugraph unused; used cuDF cuML',
    })
    const cpu = turn({
      receipts: [{ receiptId: 'r1', status: 'completed', content: { engine: { device: 'cpu' } } }],
    })
    expect(checkPills(['cudf'], ['cudf'], cpu)).toEqual({ ok: true, detail: 'pandas scikit-learn' })
  })

  it('needs the closing events last and in order', () => {
    const events = closing()
    expect(
      checkClosing(turn({ events: [events[1], events[0], events[2], events[3]] })).detail
    ).toBe(
      'ends on report.completed > run.completed > report.reference_resolution > report.metrics'
    )
    expect(checkClosing(turn({ events: events.slice(0, 3) })).ok).toBe(false)
    const unfinished = [...events.slice(0, 3), { ...events[3], state: 'failed' }]
    expect(checkClosing(turn({ events: unfinished })).detail).toBe(
      'a closing event did not complete'
    )
  })

  it('needs the export, a full replay of the stream and the reopened session', () => {
    const good = { ok: true, detail: '' }
    expect(checkReplay('job-1', turn(), { events: 8, status: 'success' }, good)).toEqual({
      ok: true,
      detail: '8 events',
    })
    expect(checkReplay('job-2', turn(), { events: 8, status: 'success' }, good).detail).toBe(
      'the export did not load'
    )
    expect(checkReplay('job-1', turn(), { events: 5, status: 'success' }, good).detail).toBe(
      'the stream replayed 5 of 8 events'
    )
    expect(checkReplay('job-1', turn(), { events: 8, status: null }, good).detail).toBe(
      'the stream ended without a status'
    )
    const reopened = { ok: false, detail: 'reopened session: timeout' }
    expect(checkReplay('job-1', turn(), { events: 8, status: 'success' }, reopened)).toBe(reopened)
  })

  it('holds each run to its budget', () => {
    expect(checkLatency(130, 120)).toEqual({ ok: false, detail: '130 s, over 120' })
    expect(checkLatency(null, 120)).toEqual({ ok: false, detail: 'no answer' })
  })
})

describe('the stream and the table', () => {
  it('counts the execution events of a finished stream and keeps its last status', () => {
    const sse = [
      'event: stream.start\ndata: {"job_id": "job-1", "after": 0}',
      'id: 1\nevent: execution.v2\ndata: {"eventKind": "run.created"}',
      'id: 2\nevent: execution.v2\ndata: {"eventKind": "run.completed"}',
      'event: artifact.update\ndata: {}',
      'event: job.status\ndata: {"status": "success"}',
      '',
    ].join('\n\n')
    expect(parseStream(sse)).toEqual({ events: 2, status: 'success' })
    expect(parseStream('')).toEqual({ events: 0, status: null })
  })

  it('prints one row per question and the deployment checks, and counts the failures', () => {
    const passing: QuestionResult = {
      id: 'market-leaders',
      jobId: 'job-1',
      seconds: 30,
      budget: 120,
      checks: {
        success: { ok: true, detail: '' },
        citations: { ok: true, detail: '1 cited' },
        pills: { ok: true, detail: 'cuDF' },
        replay: { ok: true, detail: '13 events' },
        closing: { ok: true, detail: 'enterprise-research' },
        latency: { ok: true, detail: '30 s of 120' },
      },
    }
    const slow = {
      ...passing,
      id: 'peer-network',
      checks: { ...passing.checks, latency: { ok: false, detail: '200 s, over 120' } },
    }
    expect(failed(slow)).toEqual(['latency'])
    const table = formatTable(
      [passing, slow],
      [
        ['health', { ok: true, detail: 'status ok, mode live' }],
        ['landing', { ok: false, detail: 'missing peer-network' }],
      ]
    )
    expect(table).toContain('ok   health: status ok, mode live')
    expect(table).toContain('FAIL landing: missing peer-network')
    expect(table).toMatch(/^market-leaders\s+PASS(\s+ok){6}\s+citations: 1 cited/m)
    expect(table).toMatch(
      /^peer-network\s+FAIL\s+ok\s+ok\s+ok\s+ok\s+ok\s+FAIL\s+.*latency: 200 s, over 120/m
    )
    expect(table).toContain('1 of 2 questions passed; deployment checks 1 of 2')
  })
})

describe('withRetries', () => {
  const noWait = { sleep: async () => undefined }

  it('tries again after a transient status or a network error, and returns the answer', async () => {
    const failures = [
      new HttpStatusError(502, 'GET /x answered 502'),
      new TypeError('socket hang up'),
    ]
    let calls = 0
    const answer = await withRetries(async () => {
      calls += 1
      const failure = failures.shift()
      if (failure) throw failure
      return 'ok'
    }, noWait)
    expect([answer, calls]).toEqual(['ok', 3])
  })

  it('fails at once on another status, and after its last attempt on a transient one', async () => {
    let calls = 0
    const notFound = withRetries(async () => {
      calls += 1
      throw new HttpStatusError(404, 'GET /x answered 404')
    }, noWait)
    await expect(notFound).rejects.toThrow('answered 404')
    expect(calls).toBe(1)

    calls = 0
    const unavailable = withRetries(
      async () => {
        calls += 1
        throw new HttpStatusError(503, 'GET /x answered 503')
      },
      { ...noWait, attempts: 3 }
    )
    await expect(unavailable).rejects.toThrow('answered 503')
    expect(calls).toBe(3)
  })
})
