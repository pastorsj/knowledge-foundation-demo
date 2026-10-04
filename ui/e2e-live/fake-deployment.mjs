// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * A stand-in for a deployment's API that answers each question with its recorded session, as a live job, so
 * the live test (`scripts/demo.sh test live`) can be checked without a stack, keys or model calls. Point a UI
 * server in live mode at it (ui/README.md). The packs, their questions and sources and the answers all come
 * from the packs' committed replay bundles (`recordings/pack.json`, `sources.json` and the single-turn
 * sessions); a job runs for JOB_SECONDS, then succeeds. Your data is emulated as in the smoke test
 * (e2e/fake-documents.mjs): uploaded files move one pipeline stage per status poll, and any question asked in
 * Your data gets a canned report citing Your documents and Your tables.
 *
 * Usage: [PACKS_DIR=../data/packs] [PACKS=retail,manufacturing] [FAKE_API_PORT=3997] [JOB_SECONDS=3] \
 *        node e2e-live/fake-deployment.mjs
 *
 * PACKS defaults to every pack of PACKS_DIR with a replay bundle (`recordings/index.json`).
 */

import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import path from 'node:path'
import { createDocuments, readBody } from '../e2e/fake-documents.mjs'

const PACKS_DIR = path.resolve(process.env.PACKS_DIR ?? '../data/packs')
const jobSeconds = Number(process.env.JOB_SECONDS ?? 3)

const bundled = (id) => existsSync(path.join(PACKS_DIR, id, 'recordings', 'index.json'))
const ids = (process.env.PACKS ?? '')
  .split(',')
  .map((id) => id.trim())
  .filter(Boolean)
const packIds = ids.length
  ? ids
  : readdirSync(PACKS_DIR, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && bundled(entry.name))
      .map((entry) => entry.name)
for (const id of packIds) {
  if (!bundled(id)) throw new Error(`${id} has no replay bundle in ${PACKS_DIR}`)
}

const read = (pack, file) =>
  JSON.parse(readFileSync(path.join(PACKS_DIR, pack, 'recordings', file), 'utf8'))
const session = (pack, id) => read(pack, path.join('sessions', `${id}.json`))

/** Each pack's view (`pack.json`, a `PackView`) and sources (`sources.json`), industries by title */
const PACKS = packIds
  .map((id) => ({ view: read(id, 'pack.json'), sources: read(id, 'sources.json') }))
  .sort((a, b) => a.view.title.localeCompare(b.view.title))
const WORKSPACE = {
  id: 'workspace',
  kind: 'workspace',
  title: 'Your data',
  description: 'Your uploaded documents and tables.',
  icon: 'Upload',
  status: 'empty',
  version: null,
  as_of: null,
  disclaimer: null,
  questions: [],
  examples: [],
  conversations: [],
}
const viewOf = (id) =>
  id === 'workspace' ? WORKSPACE : PACKS.find((pack) => pack.view.id === id)?.view

/** A recorded question by its text, across the packs: its pack and its single-turn session */
const byQuestion = new Map()
for (const { view } of PACKS) {
  for (const entry of read(view.id, 'index.json').sessions) {
    if (entry.turns.length === 1) {
      byQuestion.set(entry.turns[0].question.trim(), { pack: view.id, session: entry.id })
    }
  }
}

const documents = createDocuments()

/**
 * Your data's canned turn: a recorded turn's events (so it ends on the closing events), with a report citing
 * one receipt of Your documents and one of Your tables.
 */
const workspaceTurn = () => {
  const [template] = byQuestion.values()
  const turn = session(template.pack, template.session).turns[0]
  const receipt = (n, artifactKind, toolName, content) => ({
    schemaVersion: '2',
    artifactKind,
    receiptId: `fake-receipt:${n}`,
    jobId: turn.jobId,
    invocationId: `fake-tool:${n}`,
    toolName,
    status: 'completed',
    content,
  })
  return {
    ...turn,
    sourceIds: ['workspace.documents', 'workspace.tables'],
    report: {
      markdown:
        'Opened items may be returned within 30 days [1], and order O-1001 was the largest at $120 [2].\n\n' +
        '**References:**\n' +
        '- [1] Unstructured retrieval evidence — policy.pdf, p. 1 — evidence `fake-receipt:1` — invocation `fake-tool:1`\n' +
        '- [2] Structured query result — orders — evidence `fake-receipt:2` — invocation `fake-tool:2`',
      citations: [
        { number: 1, evidenceId: 'fake-receipt:1', invocationId: 'fake-tool:1' },
        { number: 2, evidenceId: 'fake-receipt:2', invocationId: 'fake-tool:2' },
      ],
    },
    receipts: [
      receipt(1, 'retrieval_evidence', 'mcp__retrieval__retrieve_evidence', {
        sourceIds: ['workspace.documents'],
        hits: [{ rank: 1, sourceId: 'workspace.documents', title: 'policy.pdf' }],
      }),
      receipt(2, 'structured_query', 'mcp__tables__query_tables', {
        databaseName: 'workspace_tables',
        sql: 'SELECT order_id, net_amount FROM orders ORDER BY net_amount DESC LIMIT 1',
      }),
    ],
  }
}

const jobs = new Map()
const statusOf = (job) => (Date.now() - job.started >= jobSeconds * 1000 ? 'success' : 'running')
/** The recorded turn (Your data's canned one), under the new job's id */
const turnOf = (job) => {
  const turn = job.workspace ? workspaceTurn() : session(job.pack, job.session).turns[0]
  return JSON.parse(JSON.stringify(turn).replaceAll(turn.jobId, job.id))
}
const json = (res, status, body) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
const sse = (event, data, id) =>
  `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
const summary = ({ id, kind, title, description, icon, status }) => ({
  id,
  kind,
  title,
  description,
  icon,
  status,
})

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://fake-deployment')
  const { pathname } = url
  const route = `${req.method} ${pathname}`
  if (route === 'GET /v1/packs') {
    return json(res, 200, {
      packs: [
        ...PACKS.map(({ view }) => summary({ ...view, status: 'ready' })),
        summary(WORKSPACE),
      ],
    })
  }
  if (route === 'GET /v1/pack') {
    const id = url.searchParams.get('id')
    const view = id ? viewOf(id) : PACKS[0]?.view
    return view ? json(res, 200, view) : json(res, 404, { detail: `Unknown pack ${id}` })
  }
  if (route === 'GET /v1/data_sources') {
    const id = url.searchParams.get('pack')
    if (id === 'workspace') return json(res, 200, documents.sources())
    if (!id)
      return json(res, 200, [...PACKS.flatMap((pack) => pack.sources), ...documents.sources()])
    const pack = PACKS.find((entry) => entry.view.id === id)
    return pack ? json(res, 200, pack.sources) : json(res, 404, { detail: `Unknown pack ${id}` })
  }
  if (await documents.handle(req, res, pathname)) return
  if (route === 'POST /v1/jobs/async/submit') {
    const request = JSON.parse((await readBody(req)).toString())
    const id = request.job_id ?? `job-${jobs.size + 1}`
    const workspace = (request.data_sources ?? []).some((source) =>
      String(source).startsWith('workspace.')
    )
    const recorded = byQuestion.get(String(request.input).trim())
    if (!workspace && !recorded) {
      return json(res, 422, { detail: 'the stand-in knows only the recorded questions' })
    }
    jobs.set(id, { id, ...recorded, workspace, started: Date.now() })
    return json(res, 200, { job_id: id, status: 'submitted' })
  }
  const match = pathname.match(/^\/v1\/jobs\/async\/job\/([^/]+)(\/[^/]+)?/)
  const job = match && jobs.get(match[1])
  if (!job) return json(res, 404, { detail: 'not found' })
  const action = `${req.method} ${match[2] ?? ''}`
  if (action === 'GET ')
    return json(res, 200, { job_id: job.id, status: statusOf(job), error: null })
  if (action === 'GET /export') return json(res, 200, { ...turnOf(job), status: statusOf(job) })
  if (action === 'POST /cancel')
    return json(res, 200, { job_id: job.id, status: 'interrupted', cancelled: true })
  if (action === 'GET /stream') {
    res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache' })
    res.write(sse('stream.start', { job_id: job.id, after: 0 }))
    while (statusOf(job) !== 'success') await new Promise((resolve) => setTimeout(resolve, 250))
    const turn = turnOf(job)
    turn.events.forEach((event, cursor) => res.write(sse('execution.v2', event, cursor + 1)))
    const report = { type: 'output', output_category: 'final_report', ...turn.report }
    res.write(sse('artifact.update', { data: { ...report, content: turn.report.markdown } }))
    return res.end(sse('job.status', { status: 'success' }))
  }
  return json(res, 404, { detail: 'not found' })
}).listen(Number(process.env.FAKE_API_PORT ?? 3997), '127.0.0.1')
