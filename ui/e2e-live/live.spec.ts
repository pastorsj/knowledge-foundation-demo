// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The live end-to-end test (`scripts/demo.sh test live --url URL`): a running deployment's health and packs,
 * then each featured question of each industry pack asked through the UI, one at a time, each in a fresh
 * browser context (a new session), and checked through the UI and the public API (checks.ts); then Your data:
 * a PDF and a CSV uploaded through the UI, watched through the pipeline, and a question that needs both asked
 * and checked for citations of each. Every question runs live and costs model calls; the uploaded files are
 * deleted at the end. Manual only: CI never runs it, and the URL is never stored.
 *
 * LIVE_URL       the deployment's UI, e.g. http://127.0.0.1:3100 (required)
 * LIVE_PACK      one pack to test (default: every industry pack of `GET /v1/packs`)
 * LIVE_QUESTIONS question ids, `id` or `pack/id`, comma-separated (default: each pack's featured questions)
 * LIVE_BUDGETS   latency budgets: SECONDS for all, ID=SECONDS or PACK/ID=SECONDS for one (default: three times
 *                the recorded run)
 * LIVE_UPLOAD    0 skips Your data's upload-then-ask run
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
  checkUploadCitations,
  failed,
  formatTable,
  HttpStatusError,
  parseBudgets,
  parseStream,
  recordedSeconds,
  type Check,
  type LiveTurn,
  type PackQuestion,
  type QuestionResult,
  selectQuestions as select,
  unmatchedQuestions,
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
const LIVE_PACK = (process.env.LIVE_PACK ?? '').trim()
const LIVE_QUESTIONS = process.env.LIVE_QUESTIONS
const UPLOAD = process.env.LIVE_UPLOAD !== '0'
/** How long the uploaded files may take to become Available (Nemotron Parse on a busy GPU is slow) */
const INGEST_SECONDS = 600
const FILES = path.resolve(process.cwd(), 'e2e', 'fixtures', 'files')
const WORKSPACE_SOURCES = ['workspace.documents', 'workspace.tables']
/** Needs the uploaded policy (30 days) and the uploaded orders (O-1001, the largest) */
const UPLOAD_QUESTION =
  'In my uploaded files: how many days does the returns policy allow for returning opened items, ' +
  'and which order in the orders table has the largest net amount?'

interface PackSummary {
  id: string
  kind?: string
  title?: string
  status?: string
}

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
    const pack = encodeURIComponent(question.pack)
    await page.goto(`/research?pack=${pack}`)
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
      await page.goto(`/research?pack=${pack}&question=${encodeURIComponent(question.id)}`)
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
    pack: question.pack,
    id: `${question.pack}/${question.id}`,
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

/** A fixture file made unique to this run (in name and bytes, so the ingest service's sha256 dedup never
 * matches a file already in Your data, and the cleanup deletes only this run's files). */
const runFile = (name: string, token: string) => {
  const [stem, extension] = name.split('.')
  const bytes = readFileSync(path.join(FILES, name))
  // A PDF comment after %%EOF, and one more (tiny) order: neither changes the answer
  const extra = extension === 'pdf' ? `\n% kf-live ${token}\n` : `O-${token},C9,2026-06-01,1.00\n`
  return {
    name: `${stem}_${token}.${extension}`,
    mimeType: extension === 'pdf' ? 'application/pdf' : 'text/csv',
    buffer: Buffer.concat([bytes, Buffer.from(extra)]),
  }
}

interface UploadRun {
  files: string[]
  fileIds: string[]
  /** The pipeline stages each file's card showed, in order */
  stages: Record<string, string[]>
  jobId: string | null
  seconds: number | null
  checks: Array<[string, Check]>
}

/**
 * Your data: upload a PDF and a CSV through the UI, wait for both to be Available, check what read them,
 * ask a question that needs both, and check its report cites each workspace source. The files are deleted
 * at the end, whatever happened.
 */
const uploadAndAsk = async (browser: Browser, request: APIRequestContext): Promise<UploadRun> => {
  const token = Date.now().toString(36)
  const [pdf, csv] = [runFile('policy.pdf', token), runFile('orders.csv', token)]
  const run: UploadRun = {
    files: [pdf.name, csv.name],
    fileIds: [],
    stages: { [pdf.name]: [], [csv.name]: [] },
    jobId: null,
    seconds: null,
    checks: [],
  }
  const context = await browser.newContext({
    baseURL: BASE,
    viewport: { width: 1440, height: 900 },
  })
  const page = await context.newPage()
  const collection = `${BASE}/api/v1/collections/workspace/documents`
  let step = 'upload'
  try {
    await page.goto('/research?pack=workspace')
    const filesTab = page.getByRole('radio', { name: 'Files' })
    if (!(await filesTab.isChecked())) await filesTab.click()
    const uploaded = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        response.url().endsWith('/api/v1/collections/workspace/documents'),
      { timeout: 120_000 }
    )
    await page.getByTestId('composer-file-input').setInputFiles([pdf, csv])
    const response = await uploaded
    if (!response.ok()) throw new Error(`the upload answered ${response.status()}`)
    run.fileIds = ((await response.json()) as { file_ids?: string[] }).file_ids ?? []
    run.checks.push(['upload', ok(`${run.fileIds.length} file(s) accepted`)])

    // Each card through its stages, to Available (or an error)
    step = 'ingest'
    const cards = page.getByTestId('file-source-card')
    const card = (name: string) => cards.filter({ hasText: name })
    const started = Date.now()
    for (;;) {
      const states = await Promise.all(
        run.files.map(async (name) => {
          // Not waiting for a card that is not there yet
          const [status, stage] = await card(name).evaluateAll((elements) => [
            elements[0]?.getAttribute('data-status') ?? null,
            elements[0]?.getAttribute('data-stage') ?? null,
          ])

          const seen = run.stages[name]
          if (stage && seen.at(-1) !== stage) seen.push(stage)
          return status
        })
      )
      if (states.every((status) => status === 'available')) break
      const broken = run.files.filter((_, index) => states[index] === 'error')
      if (broken.length) throw new Error(`${broken.join(', ')} failed to ingest`)
      if (Date.now() - started > INGEST_SECONDS * 1000) {
        throw new Error(`not Available within ${INGEST_SECONDS} s (${states.join(', ')})`)
      }
      await page.waitForTimeout(2000)
    }
    const stages = run.files.map((name) => `${name}: ${run.stages[name].join(' > ') || '-'}`)
    run.checks.push([
      'ingest',
      ok(`${Math.round((Date.now() - started) / 1000)} s; ${stages.join('; ')}`),
    ])

    // What read them: Nemotron Parse (or the PDF's text layer, with a warning saying so), and DuckDB
    step = 'parsers'
    const pdfParser = (await card(pdf.name).getByTestId('file-parser').textContent())?.trim() ?? ''
    const pdfWarned = (await card(pdf.name).getByRole('note').count()) > 0
    run.checks.push([
      'pdf parser',
      pdfParser === 'Nemotron Parse 2.0' || (pdfParser === 'PDF text layer' && pdfWarned)
        ? ok(pdfParser)
        : bad(`${pdfParser || 'none'}${pdfWarned ? ', with a warning' : ''}`),
    ])
    const csvParser = (await card(csv.name).getByTestId('file-parser').textContent())?.trim() ?? ''
    const csvText = (await card(csv.name).textContent()) ?? ''
    const table = csvText.match(/Tables?:\s*(\S+)/)?.[1]
    run.checks.push([
      'csv parser',
      csvParser === 'DuckDB CSV' && table
        ? ok(`${csvParser}, table ${table}`)
        : bad(`${csvParser || 'none'}, table ${table ?? 'none'}`),
    ])

    // Ask a question that needs both files
    step = 'ask'
    const composer = page.getByRole('textbox', { name: 'Chat message input' })
    await composer.fill(UPLOAD_QUESTION)
    const submitted = page.waitForResponse(
      (answer) =>
        answer.request().method() === 'POST' && answer.url().endsWith('/api/v1/jobs/async/submit'),
      { timeout: 60_000 }
    )
    const asked = Date.now()
    await page.getByRole('button', { name: 'Send message' }).click()
    const answer = await submitted
    if (!answer.ok()) throw new Error(`the submit answered ${answer.status()}`)
    run.jobId = String(((await answer.json()) as { job_id?: string }).job_id)
    const status = await waitForJob(request, run.jobId)
    run.seconds = (Date.now() - asked) / 1000
    const job = `${BASE}/api/v1/jobs/async/job/${encodeURIComponent(run.jobId)}`
    const turn = (await getJson(request, `${job}/export`)) as LiveTurn
    const sources = (await getJson(
      request,
      `${BASE}/api/v1/data_sources?pack=workspace`
    )) as Array<{
      id: string
      database_name?: string | null
    }>
    const aliases = Object.fromEntries(
      sources.flatMap((source) => (source.database_name ? [[source.database_name, source.id]] : []))
    )
    run.checks.push(
      ['success', checkSuccess(status, turn)],
      ['closing', checkClosing(turn)],
      ['citations', checkUploadCitations(turn, WORKSPACE_SOURCES, aliases)]
    )
  } catch (error) {
    run.checks.push([step, bad(message(error))])
    if (run.jobId) {
      await request
        .post(`${BASE}/api/v1/jobs/async/job/${encodeURIComponent(run.jobId)}/cancel`)
        .catch(() => undefined)
    }
  } finally {
    await context.close()
    // This run's files: by the upload's answer, else by their names in the collection
    const ids = run.fileIds.length
      ? run.fileIds
      : await getJson(request, collection)
          .then((body) =>
            ((body as { files?: Array<{ file_id: string; file_name: string }> }).files ?? [])
              .filter((file) => run.files.includes(file.file_name))
              .map((file) => file.file_id)
          )
          .catch(() => [] as string[])
    if (ids.length) {
      const deleted = await request
        .delete(collection, { data: { file_ids: ids } })
        .catch(() => null)
      run.checks.push([
        'cleanup',
        deleted?.ok()
          ? ok(`${ids.length} file(s) deleted`)
          : bad(`DELETE answered ${deleted?.status() ?? 'nothing'}`),
      ])
    }
  }
  return run
}

/** The deployment's packs, or why it has none to test (the catalog is not built yet, a 503). */
const listPacks = async (request: APIRequestContext): Promise<PackSummary[] | Check> => {
  try {
    return (
      ((await getJson(request, `${BASE}/api/v1/packs`)) as { packs?: PackSummary[] }).packs ?? []
    )
  } catch (error) {
    return error instanceof HttpStatusError && error.status === 503
      ? bad(
          'knowledge catalog not built yet (GET /v1/packs answered 503; ingest is syncing the packs)'
        )
      : bad(message(error))
  }
}

test('each industry’s featured questions and Your data’s upload, live through the UI and the API', async ({
  browser,
  request,
}, testInfo) => {
  expect(BASE, 'set LIVE_URL (demo.sh test live --url URL)').toMatch(/^https?:\/\/./)
  // Until the questions are counted; then as long as they may take
  test.setTimeout(30 * 60_000)
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

  // The packs: every industry, or LIVE_PACK
  const listed = await listPacks(request)
  const packs = Array.isArray(listed) ? listed : []
  if (!Array.isArray(listed)) deployment.push(['catalog', listed])
  const industries = packs.filter((pack) => pack.kind === 'industry')
  const chosen = LIVE_PACK ? industries.filter((pack) => pack.id === LIVE_PACK) : industries
  if (Array.isArray(listed)) {
    deployment.push([
      'catalog',
      LIVE_PACK && LIVE_PACK !== 'workspace' && !chosen.length
        ? bad(
            `no industry ${LIVE_PACK}; the deployment offers ${industries.map((p) => p.id).join(', ')}`
          )
        : industries.length
          ? ok(`${industries.length} industries: ${industries.map((p) => p.id).join(', ')}`)
          : bad('no industry packs'),
    ])
  }

  const questions: PackQuestion[] = []
  for (const summary of chosen) {
    const id = summary.id
    const pack = (await getJson(request, `${BASE}/api/v1/pack?id=${encodeURIComponent(id)}`)) as {
      id: string
      version?: string
      questions?: Array<Omit<PackQuestion, 'pack'>>
    }
    const offered = (pack.questions ?? []).map((question) => ({ ...question, pack: id }))
    let selected: PackQuestion[]
    try {
      selected = select(offered, LIVE_QUESTIONS, id)
    } catch (error) {
      deployment.push([`pack ${id}`, bad(message(error))])
      continue
    }
    // Asked for other packs' questions: this one is not tested
    if (LIVE_QUESTIONS && !selected.length) continue
    const sources = new Set(
      (
        (await getJson(
          request,
          `${BASE}/api/v1/data_sources?pack=${encodeURIComponent(id)}`
        )) as Array<{ id: string; status?: string | null }>
      ).map((source) => source.id)
    )
    const unavailable = selected.filter((q) => !q.sources.every((source) => sources.has(source)))
    deployment.push([
      `pack ${id} ${pack.version ?? ''}`.trim(),
      summary.status && summary.status !== 'ready'
        ? bad(`${summary.status}, not ready (ingest is still syncing it, or it failed)`)
        : !selected.length
          ? bad('no featured questions')
          : unavailable.length
            ? bad(`sources missing for ${unavailable.map((q) => q.id).join(', ')}`)
            : ok(`${selected.length} question(s): ${selected.map((q) => q.id).join(', ')}`),
    ])
    questions.push(...selected)

    // Its landing page lists its featured questions
    const landing = await browser.newPage({ baseURL: BASE })
    const featured = offered.filter((q) => q.featured)
    const missing: string[] = []
    await landing.goto(`/?pack=${encodeURIComponent(id)}`)
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
      `landing ${id}`,
      missing.length ? bad(`missing ${missing.join(', ')}`) : ok(`${featured.length} featured`),
    ])
  }
  const unmatched = unmatchedQuestions(LIVE_QUESTIONS, questions)
  if (unmatched.length) {
    deployment.push(['questions', bad(`no tested pack offers ${unmatched.join(', ')}`)])
  }

  const budgets = parseBudgets(process.env.LIVE_BUDGETS)
  test.setTimeout(
    (questions.length * (HARD_CAP_SECONDS + 4 * UI_SECONDS) +
      (UPLOAD ? INGEST_SECONDS + HARD_CAP_SECONDS + 2 * UI_SECONDS : 0) +
      600) *
      1000
  )
  const results: QuestionResult[] = []
  for (const question of questions) {
    const name = `${question.pack}/${question.id}`
    const budget = budgetFor(
      question.id,
      budgets,
      recorded(question.pack, question.id),
      question.pack
    )
    process.stdout.write(`asking ${name} (budget ${budget} s)\n`)
    const result = await askOne(browser, request, question, budget)
    const failures = failed(result)
    process.stdout.write(
      `${name}: ${failures.length ? `FAIL (${failures.join(', ')})` : 'PASS'} in ${
        result.seconds === null ? '-' : Math.round(result.seconds)
      } s, job ${result.jobId ?? '-'}\n`
    )
    results.push(result)
  }

  let upload: UploadRun | null = null
  if (UPLOAD) {
    process.stdout.write('Your data: uploading a PDF and a CSV, then asking about both\n')
    upload = await uploadAndAsk(browser, request)
    const failures = upload.checks.filter(([, check]) => !check.ok).map(([name]) => name)
    process.stdout.write(`upload: ${failures.length ? `FAIL (${failures.join(', ')})` : 'PASS'}\n`)
  }

  const table = formatTable(results, deployment, upload?.checks ?? [])
  const saved = testInfo.outputPath('live-results.json')
  writeFileSync(
    saved,
    JSON.stringify({ packs: chosen.map((pack) => pack.id), deployment, results, upload }, null, 2)
  )
  process.stdout.write(`\n${table}\nResults: ${saved}\n`)
  const failing = [
    ...deployment.filter(([, check]) => !check.ok).map(([name]) => name),
    ...results.filter((result) => failed(result).length).map((result) => result.id),
    ...(upload?.checks ?? []).filter(([, check]) => !check.ok).map(([name]) => `upload ${name}`),
  ]
  expect(
    questions.length || upload,
    'nothing to test: no question selected and LIVE_UPLOAD=0'
  ).toBeTruthy()
  expect(failing, 'the failing questions and deployment checks (see the table above)').toEqual([])
})
