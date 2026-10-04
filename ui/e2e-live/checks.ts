// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * What one live run of a featured question must show (`scripts/demo.sh test live`), as pure functions of what the
 * deployment returned, so they are unit-tested (checks.test.ts) apart from the browser run (live.spec.ts):
 *
 * - success: the job ended `success`;
 * - citations: the report cites at least one receipt of the run;
 * - pills: the picker shows the pills the question declares, and the run used every one of them (the pills its
 *   replay shows, `sessionPills`);
 * - replay: the job's export loads (the turn the UI reopens a session from), its event stream replays every event
 *   and ends `success`, and the reopened session's execution view shows the run's closing events;
 * - closing: the run ends on run.completed, report.completed, report.reference_resolution and report.metrics;
 * - latency: the job finished within its budget.
 *
 * Your data's upload-then-ask run is checked by checkUploadCitations: its report cites a receipt of each
 * workspace source.
 */

import { pillLabel, sessionPills } from '@/shared/components/ToolPills/pills'

export interface PackQuestion {
  /** The pack it belongs to (the live test adds it to the API's questions) */
  pack: string
  id: string
  label?: string
  question: string
  sources: string[]
  tools: string[]
  featured?: boolean
}

export interface LiveEvent {
  eventKind?: string
  state?: string
  toolName?: string | null
  artifactRefs?: string[]
  display?: { label?: string; attributes?: Record<string, unknown> }
}

export interface LiveTurn {
  jobId?: string
  status?: string
  report?: { markdown?: string; citations?: Array<{ evidenceId?: string }> } | null
  events?: LiveEvent[]
  receipts?: Array<{
    receiptId?: string
    status?: string
    artifactKind?: string
    content?: unknown
  }>
}

export interface Check {
  ok: boolean
  detail: string
}

export const CHECKS = ['success', 'citations', 'pills', 'replay', 'closing', 'latency'] as const
export type CheckId = (typeof CHECKS)[number]

export interface QuestionResult {
  pack: string
  /** `pack/question` */
  id: string
  jobId: string | null
  seconds: number | null
  budget: number
  checks: Record<CheckId, Check>
}

/** The events a successful run ends on, in this order (api/src/demo_api/hermes/normalizer.py). */
export const CLOSING_EVENTS = [
  'run.completed',
  'report.completed',
  'report.reference_resolution',
  'report.metrics',
] as const

/** Without a recording to go by; the job deadline is 1,200 s. */
export const FALLBACK_BUDGET_SECONDS = 300
export const MIN_BUDGET_SECONDS = 120
export const MAX_BUDGET_SECONDS = 600

const pass = (detail = ''): Check => ({ ok: true, detail })
const fail = (detail: string): Check => ({ ok: false, detail })

/** The requested questions: ids, comma- or space-separated, each `id` or `pack/id`. */
export const parseQuestionFilter = (requested: string | undefined): string[] =>
  (requested ?? '')
    .split(/[\s,]+/)
    .map((id) => id.trim())
    .filter(Boolean)

/**
 * One pack's questions to ask: its featured ones, or those requested (`id` or `pack/id`) in the order asked. A
 * bare id another pack offers is not this pack's; `pack/id` naming a question this pack lacks is an error.
 */
export const selectQuestions = (
  questions: readonly PackQuestion[],
  requested: string | undefined,
  pack: string
): PackQuestion[] => {
  const ids = parseQuestionFilter(requested)
  if (!ids.length) return questions.filter((question) => question.featured)
  const byId = new Map(questions.map((question) => [question.id, question]))
  const unknown = ids.filter(
    (id) => id.startsWith(`${pack}/`) && !byId.has(id.slice(pack.length + 1))
  )
  if (unknown.length) {
    throw new Error(
      `the pack ${pack} has no question ${unknown.join(', ')}; it offers ${[...byId.keys()].join(', ')}`
    )
  }
  const selected = ids
    .map((id) =>
      id.startsWith(`${pack}/`) ? id.slice(pack.length + 1) : id.includes('/') ? '' : id
    )
    .flatMap((id) => (byId.has(id) ? [byId.get(id) as PackQuestion] : []))
  return [...new Set(selected)]
}

/** The requested questions (`id` or `pack/id`) that no selected question answers to. */
export const unmatchedQuestions = (
  requested: string | undefined,
  selected: readonly PackQuestion[]
): string[] =>
  parseQuestionFilter(requested).filter(
    (id) =>
      !selected.some((question) => id === question.id || id === `${question.pack}/${question.id}`)
  )

/**
 * `300`, `return-window-fees=60`, `retail/return-window-fees=60` or any of them, comma-separated: one budget
 * for all, and budgets by question (`id` or `pack/id`).
 */
export const parseBudgets = (
  spec: string | undefined
): { all: number | null; byQuestion: Record<string, number> } => {
  const budgets = { all: null as number | null, byQuestion: {} as Record<string, number> }
  for (const part of (spec ?? '').split(',').map((item) => item.trim())) {
    if (!part) continue
    const [id, value] = part.includes('=') ? part.split('=', 2) : [null, part]
    const seconds = Number(value)
    if (!Number.isFinite(seconds) || seconds <= 0 || (id !== null && !id.trim())) {
      throw new Error(`not a budget: ${part} (use SECONDS or QUESTION_ID=SECONDS)`)
    }
    if (id === null) budgets.all = seconds
    else budgets.byQuestion[id.trim()] = seconds
  }
  return budgets
}

/** Three times the recorded run, rounded up to 30 s, kept between 2 and 10 minutes; 5 minutes without one. */
export const defaultBudget = (recordedSeconds: number | null): number => {
  if (recordedSeconds === null || !Number.isFinite(recordedSeconds) || recordedSeconds <= 0) {
    return FALLBACK_BUDGET_SECONDS
  }
  const budget = Math.ceil((3 * recordedSeconds) / 30) * 30
  return Math.min(MAX_BUDGET_SECONDS, Math.max(MIN_BUDGET_SECONDS, budget))
}

/** The budget of `pack/id`, else of `id`, else for all, else three times the recorded run. */
export const budgetFor = (
  id: string,
  budgets: ReturnType<typeof parseBudgets>,
  recordedSeconds: number | null,
  pack?: string
): number =>
  (pack ? budgets.byQuestion[`${pack}/${id}`] : undefined) ??
  budgets.byQuestion[id] ??
  budgets.all ??
  defaultBudget(recordedSeconds)

/** How long the recorded session's last turn took (its report.metrics `wall_duration_ms`), in seconds. */
export const recordedSeconds = (session: unknown): number | null => {
  const turns = (session as { turns?: LiveTurn[] } | null)?.turns ?? []
  const metrics = (turns.at(-1)?.events ?? []).find((event) => event.eventKind === 'report.metrics')
  const ms = metrics?.display?.attributes?.wall_duration_ms
  return typeof ms === 'number' && ms > 0 ? ms / 1000 : null
}

/** Answers worth another try, as the eval client has them: the queue is full, or a proxy or tunnel on the way failed. */
export const TRANSIENT_STATUSES: ReadonlySet<number> = new Set([429, 502, 503, 504])

/** A response with an HTTP status the request does not accept. */
export class HttpStatusError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/**
 * `request`, tried again after a transient status or a network error (no answer at all), with a growing pause; any
 * other status fails at once. One failed poll of a twenty-minute job must not fail its question.
 */
export const withRetries = async <T>(
  request: () => Promise<T>,
  {
    attempts = 4,
    pauseMs = (attempt: number) => 5000 * attempt,
    sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)),
  }: {
    attempts?: number
    pauseMs?: (attempt: number) => number
    sleep?: (ms: number) => Promise<void>
  } = {}
): Promise<T> => {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await request()
    } catch (error) {
      const transient = !(error instanceof HttpStatusError) || TRANSIENT_STATUSES.has(error.status)
      if (!transient || attempt >= attempts) throw error
    }
    await sleep(pauseMs(attempt))
  }
}

export const checkSuccess = (status: string, turn: LiveTurn | null): Check =>
  status === 'success' && turn?.status === 'success'
    ? pass()
    : fail(
        `job ${status}${turn?.status && turn.status !== status ? `, export ${turn.status}` : ''}`
      )

/** At least one citation, and every citation names a completed receipt of the run. */
export const checkCitations = (turn: LiveTurn | null): Check => {
  const receipts = new Set(
    (turn?.receipts ?? []).filter((r) => r.status === 'completed').map((r) => r.receiptId)
  )
  const citations = turn?.report?.citations ?? []
  const resolved = citations.filter((citation) => receipts.has(citation.evidenceId)).length
  if (!resolved)
    return fail(citations.length ? 'no citation resolves to a receipt' : 'no citations')
  if (resolved < citations.length)
    return fail(`${citations.length - resolved} citation(s) unresolved`)
  return pass(`${resolved} cited`)
}

/**
 * The sources a receipt is evidence from: a retrieval's hits (else the sources it searched), and a structured
 * query's database, by its alias (`aliases`, `database_name` to source id; `workspace_tables` is
 * `workspace.tables` without one).
 */
export const receiptSources = (
  content: unknown,
  aliases: Readonly<Record<string, string>> = {}
): string[] => {
  const body = (content ?? {}) as {
    sourceIds?: unknown
    hits?: Array<{ sourceId?: unknown }>
    databaseName?: unknown
  }
  const hits = (Array.isArray(body.hits) ? body.hits : [])
    .map((hit) => hit?.sourceId)
    .filter((id): id is string => typeof id === 'string')
  const searched = Array.isArray(body.sourceIds)
    ? body.sourceIds.filter((id): id is string => typeof id === 'string')
    : []
  const database =
    typeof body.databaseName === 'string'
      ? [aliases[body.databaseName] ?? body.databaseName.replace('_', '.')]
      : []
  return [...new Set([...(hits.length ? hits : searched), ...database])]
}

/** Every citation resolves, and the cited receipts include evidence from each of the `required` sources. */
export const checkUploadCitations = (
  turn: LiveTurn | null,
  required: readonly string[],
  aliases: Readonly<Record<string, string>> = {}
): Check => {
  const cited = checkCitations(turn)
  if (!cited.ok) return cited
  const evidence = new Set((turn?.report?.citations ?? []).map((citation) => citation.evidenceId))
  const sources = new Set(
    (turn?.receipts ?? [])
      .filter((receipt) => evidence.has(receipt.receiptId))
      .flatMap((receipt) => receiptSources(receipt.content, aliases))
  )
  const missing = required.filter((source) => !sources.has(source))
  return missing.length
    ? fail(
        `no cited evidence from ${missing.join(', ')}; cited ${[...sources].join(', ') || 'none'}`
      )
    : pass(`${cited.detail} from ${required.join(', ')}`)
}

/** The picker's pills are the declared ones, and the run used every declared pill. */
export const checkPills = (
  declared: string[],
  shown: string[] | null,
  turn: LiveTurn | null
): Check => {
  const used = sessionPills([{ events: turn?.events ?? [] }])
  const usedKinds = new Set<string>(used.map((use) => use.pill))
  const labels = used.map(pillLabel).join(' ') || 'none'
  if (shown !== null && [...shown].sort().join() !== [...declared].sort().join()) {
    return fail(
      `picker shows ${shown.join(' ') || 'none'}, declared ${declared.join(' ') || 'none'}`
    )
  }
  const missing = declared.filter((pill) => !usedKinds.has(pill))
  return missing.length
    ? fail(`declared ${missing.join(' ')} unused; used ${labels}`)
    : pass(labels)
}

/** The run's last events, heartbeats aside, are the closing events in order, each completed. */
export const checkClosing = (turn: LiveTurn | null): Check => {
  const events = (turn?.events ?? []).filter((event) => event.eventKind !== 'run.heartbeat')
  const tail = events.slice(-CLOSING_EVENTS.length)
  const kinds = tail.map((event) => event.eventKind)
  if (kinds.join() !== CLOSING_EVENTS.join()) {
    return fail(`ends on ${kinds.join(' > ') || 'nothing'}`)
  }
  if (tail.some((event) => event.state !== 'completed'))
    return fail('a closing event did not complete')
  const profile = tail.at(-1)?.display?.attributes?.runtime_profile
  return pass(typeof profile === 'string' ? profile : '')
}

/**
 * The replay: the export holds the job's turn, the event stream (`/stream`, from the start) replays every one of
 * its events and ends `success`, and the reopened session's execution view showed the closing events.
 */
export const checkReplay = (
  jobId: string,
  turn: LiveTurn | null,
  stream: { events: number; status: string | null } | null,
  reopened: Check
): Check => {
  if (!turn || turn.jobId !== jobId) return fail('the export did not load')
  if (!turn.events?.length) return fail('the export has no events')
  if (!stream) return fail('the event stream did not load')
  if (stream.events !== turn.events.length) {
    return fail(`the stream replayed ${stream.events} of ${turn.events.length} events`)
  }
  if (stream.status !== 'success')
    return fail(`the stream ended ${stream.status ?? 'without a status'}`)
  return reopened.ok ? pass(`${stream.events} events`) : reopened
}

export const checkLatency = (seconds: number | null, budget: number): Check =>
  seconds === null
    ? fail('no answer')
    : seconds <= budget
      ? pass(`${Math.round(seconds)} s of ${budget}`)
      : fail(`${Math.round(seconds)} s, over ${budget}`)

/** The `execution.v2` records and the final `job.status` of a finished job's Server-Sent Events. */
export const parseStream = (text: string): { events: number; status: string | null } => {
  let events = 0
  let status: string | null = null
  for (const frame of text.split(/\r?\n\r?\n/)) {
    const name = frame.match(/^event: (.+)$/m)?.[1]?.trim()
    const data = frame.match(/^data: (.*)$/m)?.[1]
    if (name === 'execution.v2') events += 1
    if (name === 'job.status' && data) {
      try {
        status = (JSON.parse(data) as { status?: string }).status ?? status
      } catch {
        // a malformed frame leaves the last status
      }
    }
  }
  return { events, status }
}

export const failed = (result: QuestionResult): CheckId[] =>
  CHECKS.filter((check) => !result.checks[check].ok)

/** A compact pass/fail table: one row per question, then the deployment's own checks and the upload's. */
export const formatTable = (
  results: readonly QuestionResult[],
  deployment: ReadonlyArray<readonly [string, Check]>,
  upload: ReadonlyArray<readonly [string, Check]> = []
): string => {
  const header = ['Pack', 'Question', 'Result', ...CHECKS, 'Details']
  const rows = results.map((result) => [
    result.pack,
    result.id.startsWith(`${result.pack}/`) ? result.id.slice(result.pack.length + 1) : result.id,
    failed(result).length ? 'FAIL' : 'PASS',
    ...CHECKS.map((check) => (result.checks[check].ok ? 'ok' : 'FAIL')),
    CHECKS.filter((check) => result.checks[check].detail)
      .map((check) => `${check}: ${result.checks[check].detail}`)
      .join('; '),
  ])
  const widths = header.map((title, column) =>
    Math.max(
      title.length,
      ...rows.map((row) => (column === header.length - 1 ? 0 : row[column].length))
    )
  )
  const line = (cells: string[]) =>
    cells
      .map((cell, column) => (column === cells.length - 1 ? cell : cell.padEnd(widths[column])))
      .join('  ')
  const named = (checks: ReadonlyArray<readonly [string, Check]>, prefix = '') =>
    checks.map(([name, check]) => `${check.ok ? 'ok  ' : 'FAIL'} ${prefix}${name}: ${check.detail}`)
  const passed = results.filter((result) => !failed(result).length).length
  const checksOk = deployment.filter(([, check]) => check.ok).length
  const uploadOk = upload.filter(([, check]) => check.ok).length
  return [
    ...named(deployment),
    '',
    line(header),
    ...rows.map(line),
    ...(upload.length ? ['', ...named(upload, 'upload ')] : []),
    '',
    `${passed} of ${results.length} questions passed; deployment checks ${checksOk} of ${deployment.length}` +
      (upload.length ? `; upload checks ${uploadOk} of ${upload.length}` : ''),
  ].join('\n')
}
