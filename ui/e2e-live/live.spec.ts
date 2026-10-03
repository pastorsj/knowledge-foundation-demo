// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The live end-to-end test (`scripts/demo.sh test live --url URL`): a running deployment's health and pack, then
 * each featured question of its active pack asked through the UI, one at a time, each in a fresh browser context
 * (a new session), and checked through the UI and the public API (checks.ts). Every question runs live and costs
 * model calls. Manual only: CI never runs it, and the URL is never stored.
 *
 * LIVE_URL       the deployment's UI, e.g. http://127.0.0.1:3100 (required)
 * LIVE_QUESTIONS question ids, comma-separated (default: the pack's featured questions)
 * LIVE_BUDGETS   latency budgets: SECONDS for all, ID=SECONDS for one (default: three times the recorded run)
 */

import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test, type APIRequestContext, type Browser, type Page } from '@playwright/test'
import {
  budgetFor,
  checkCitations,
  checkClosing,
  checkLatency,
  checkPills,
  checkReplay,
  checkSuccess,
  failed,
  formatTable,
  HttpStatusError,
  parseBudgets,
  parseStream,
  recordedSeconds,
  selectQuestions,
  type Check,
  type LiveTurn,
  type PackQuestion,
  type QuestionResult,
  withRetries,
} from './checks'

const BASE = (process.env.LIVE_URL ?? '').replace(/\/+$/, '')
/** demo.sh runs Playwright from ui/ */
const PACKS_DIR = path.resolve(process.cwd(), '..', 'data', 'packs')
/** The API's job deadline (1,200 s) and a minute for it to report */
const HARD_CAP_SECONDS = 1260
/** After the job ends, how long the UI may take to show the answer and its execution view */
const UI_SECONDS = 120
const TERMINAL = new Set(['success', 'failure', 'interrupted'])

const ok = (detail = ''): Check => ({ ok: true, detail })
const bad = (detail: string): Check => ({ ok: false, detail })
const message = (error: unknown) =>
  (error instanceof Error ? error.message : String(error)).split('\n')[0]

/** The recorded run of a question in this checkout's bundle, for its default budget. */
const recorded = (pack: string, id: string): number | null => {
  try {
    const file = path.join(PACKS_DIR, pack, 'recordings', 'sessions', `${id}.json`)
    return recordedSeconds(JSON.parse(readFileSync(file, 'utf8')))
  } catch {
    return null
  }
}

/** A GET of the deployment's API, tried again after a transient status or a network error (checks.ts). */
const getOk = (request: APIRequestContext, url: string) =>
  withRetries(async () => {
    const response = await request.get(url, { timeout: 120_000 })
    if (!response.ok()) {
      throw new HttpStatusError(
        response.status(),
        `GET ${new URL(url).pathname} answered ${response.status()}`
      )
    }
    return response
  })

const getJson = async (request: APIRequestContext, url: string): Promise<unknown> =>
  (await getOk(request, url)).json()

/** Poll the job until it ends; past the hard cap it is cancelled and reported as stalled. */
const waitForJob = async (request: APIRequestContext, jobId: string): Promise<string> => {
  const deadline = Date.now() + HARD_CAP_SECONDS * 1000
  const job = `${BASE}/api/v1/jobs/async/job/${encodeURIComponent(jobId)}`
  for (;;) {
    const status = String(((await getJson(request, job)) as { status?: string }).status)
    if (TERMINAL.has(status)) return status
    if (Date.now() > deadline) {
      await request.post(`${job}/cancel`).catch(() => undefined)
      return 'stalled'
    }
    await new Promise((resolve) => setTimeout(resolve, 3000))
  }
}

/** The run's closing events in the execution view of the session (as answered, or reopened). */
const executionShowsClosing = async (page: Page): Promise<Check> => {
  await page
    .getByRole('button', { name: 'View execution for this response' })
    .last()
    .click({ timeout: UI_SECONDS * 1000 })
  const workspace = page.getByRole('region', { name: 'Execution workspace' })
  await expect(workspace).toContainText('Run metrics available', { timeout: UI_SECONDS * 1000 })
  return ok()
}

const askOne = async (
  browser: Browser,
  request: APIRequestContext,
  question: PackQuestion,
  budget: number
): Promise<QuestionResult> => {
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
  })
  const page = await context.newPage()
  let jobId: string | null = null
  let seconds: number | null = null
  let status = 'not asked'
  let shown: string[] | null = null
  let turn: LiveTurn | null = null
  let stream: { events: number; status: string | null } | null = null
  let answered: Check = bad('not reached')
  let reopened: Check = bad('not reached')
  try {
    // Pick the question in the composer's scenario picker, as a visitor does, and read its pills.
    // The picker offers the pack's examples only; any other question opens as a landing link does.
    await page.goto('/research')
    await page.getByTestId('demo-scenario-select').click()
    await expect(page.getByRole('option').first()).toBeVisible()
    const option = page.locator(`[data-scenario-id="${question.id}"]`)
    if (await option.count()) {
      shown = await option
        .locator('.tool-pill')
        .evaluateAll((pills) => pills.map((pill) => pill.getAttribute('data-pill') ?? ''))
      await option.click()
    } else {
      await page.keyboard.press('Escape')
      await page.goto(`/research?question=${encodeURIComponent(question.id)}`)
    }
    const composer = page.getByRole('textbox', { name: 'Chat message input' })
    await expect(composer).toHaveValue(question.question.trim())

    const submitted = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url().endsWith('/api/v1/jobs/async/submit'),
      { timeout: 60_000 }
    )
    const started = Date.now()
    await page.getByRole('button', { name: 'Send message' }).click()
    const answer = await submitted
    if (!answer.ok()) throw new Error(`the submit answered ${answer.status()}`)
    jobId = String(((await answer.json()) as { job_id?: string }).job_id)
    status = await waitForJob(request, jobId)
    seconds = (Date.now() - started) / 1000

    const job = `${BASE}/api/v1/jobs/async/job/${encodeURIComponent(jobId)}`
    turn = (await getJson(request, `${job}/export`)) as LiveTurn
    const events = await getOk(request, `${job}/stream`).catch(() => null)
    stream = events ? parseStream(await events.text()) : null

    if (status === 'success') {
      answered = await executionShowsClosing(page).catch((error) =>
        bad(`execution view: ${message(error)}`)
      )
      // Reopen the session after a reload: its execution view loads from the job's export
      await page.reload()
      const expand = page.getByRole('button', { name: 'Expand sessions sidebar' })
      if (await expand.isVisible().catch(() => false)) await expand.click()
      await page
        .getByRole('button', { name: /^Session: / })
        .first()
        .click({ timeout: 30_000 })
      reopened = answered.ok
        ? await executionShowsClosing(page).catch((error) =>
            bad(`reopened session: ${message(error)}`)
          )
        : answered
    }
  } catch (error) {
    // Where it stopped: before the job ended, the job fails; after, the replay does
    if (status === 'not asked') {
      status = `error: ${message(error)}`
      // A job left running would still hold the deployment while the next question is timed
      if (jobId) {
        await request
          .post(`${BASE}/api/v1/jobs/async/job/${encodeURIComponent(jobId)}/cancel`)
          .catch(() => undefined)
      }
    } else if (!reopened.ok) reopened = bad(message(error))
  } finally {
    await context.close()
  }
  return {
    id: question.id,
    jobId,
    seconds,
    budget,
    checks: {
      success: checkSuccess(status, turn),
      citations: checkCitations(turn),
      pills: checkPills(question.tools, shown, turn),
      replay: jobId ? checkReplay(jobId, turn, stream, reopened) : bad('no job'),
      closing: checkClosing(turn),
      latency: checkLatency(seconds, budget),
    },
  }
}

test('the active pack’s featured questions, live through the UI and the API', async ({
  browser,
  request,
}, testInfo) => {
  expect(BASE, 'set LIVE_URL (demo.sh test live --url URL)').toMatch(/^https?:\/\/./)
  const deployment: Array<[string, Check]> = []

  const health = (await getJson(request, `${BASE}/api/health`)) as {
    status?: string
    mode?: string
  }
  deployment.push([
    'health',
    health.status === 'ok' && health.mode === 'live'
      ? ok('status ok, mode live')
      : bad(`status ${health.status}, mode ${health.mode}`),
  ])

  const pack = (await getJson(request, `${BASE}/api/v1/pack`)) as {
    id: string
    version?: string
    questions?: PackQuestion[]
  }
  const sources = new Set(
    ((await getJson(request, `${BASE}/api/v1/data_sources`)) as Array<{ id: string }>).map(
      (s) => s.id
    )
  )
  const questions = selectQuestions(pack.questions ?? [], process.env.LIVE_QUESTIONS)
  const unavailable = questions.filter((q) => !q.sources.every((source) => sources.has(source)))
  deployment.push([
    `pack ${pack.id} ${pack.version ?? ''}`.trim(),
    !questions.length
      ? bad('no featured questions')
      : unavailable.length
        ? bad(`sources missing for ${unavailable.map((q) => q.id).join(', ')}`)
        : ok(`${questions.length} question(s): ${questions.map((q) => q.id).join(', ')}`),
  ])

  // The landing page lists the featured questions
  const landing = await browser.newPage({ baseURL: BASE })
  const featured = (pack.questions ?? []).filter((q) => q.featured)
  const missing: string[] = []
  await landing.goto('/')
  for (const question of featured) {
    const link = landing.getByRole('link', { name: new RegExp(question.label ?? question.id) })
    if (
      !(await link
        .first()
        .isVisible({ timeout: 30_000 })
        .catch(() => false))
    )
      missing.push(question.id)
  }
  await landing.close()
  deployment.push([
    'landing',
    missing.length ? bad(`missing ${missing.join(', ')}`) : ok(`${featured.length} featured`),
  ])

  const budgets = parseBudgets(process.env.LIVE_BUDGETS)
  test.setTimeout((questions.length * (HARD_CAP_SECONDS + 4 * UI_SECONDS) + 300) * 1000)
  const results: QuestionResult[] = []
  for (const question of questions) {
    const budget = budgetFor(question.id, budgets, recorded(pack.id, question.id))
    process.stdout.write(`asking ${question.id} (budget ${budget} s)\n`)
    const result = await askOne(browser, request, question, budget)
    const failures = failed(result)
    process.stdout.write(
      `${question.id}: ${failures.length ? `FAIL (${failures.join(', ')})` : 'PASS'} in ${
        result.seconds === null ? '-' : Math.round(result.seconds)
      } s, job ${result.jobId ?? '-'}\n`
    )
    results.push(result)
  }

  const table = formatTable(results, deployment)
  const saved = testInfo.outputPath('live-results.json')
  writeFileSync(saved, JSON.stringify({ pack: pack.id, deployment, results }, null, 2))
  process.stdout.write(`\n${table}\nResults: ${saved}\n`)
  const failing = [
    ...deployment.filter(([, check]) => !check.ok).map(([name]) => name),
    ...results.filter((result) => failed(result).length).map((result) => result.id),
  ]
  expect(failing, 'the failing questions and deployment checks (see the table above)').toEqual([])
})
