// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * A stand-in for a deployment's API that answers each question with its recorded session, as a live job, so
 * the live test (`scripts/demo.sh test live`) can be checked without a stack, keys or model calls. Point a UI
 * server in live mode at it (ui/README.md). The pack, its questions and the answers all come from a pack's
 * committed recordings (single-turn sessions only); a job runs for JOB_SECONDS, then succeeds.
 *
 * Usage: [PACKS_DIR=../data/packs] [DATA_PACK=synthetic-market] [FAKE_API_PORT=3997] [JOB_SECONDS=3] \
 *        node e2e-live/fake-deployment.mjs
 */

import { readFileSync } from 'node:fs'
import { createServer } from 'node:http'
import path from 'node:path'

const recordings = path.resolve(
  process.env.PACKS_DIR ?? '../data/packs',
  process.env.DATA_PACK ?? 'synthetic-market',
  'recordings'
)
const read = (file) => JSON.parse(readFileSync(path.join(recordings, file), 'utf8'))
const manifest = read('pack.json')
const index = read('index.json')
const session = (id) => read(path.join('sessions', `${id}.json`))
const jobSeconds = Number(process.env.JOB_SECONDS ?? 3)

const questions = index.sessions
  .filter((entry) => entry.turns.length === 1)
  .map((entry) => ({
    id: entry.id,
    label: entry.title,
    question: entry.turns[0].question,
    sources: session(entry.id).turns[0].sourceIds ?? [],
    tools: [...new Set((entry.tools ?? []).map((use) => use.pill))],
    featured: entry.featured,
  }))
const PACK = {
  ...Object.fromEntries(
    ['id', 'version', 'title', 'description', 'as_of', 'disclaimer'].map((key) => [
      key,
      manifest[key],
    ])
  ),
  questions,
  conversations: [],
}
const SOURCES = manifest.sources.map((source) => ({
  id: source.id,
  name: source.name,
  description: source.description,
  default_enabled: true,
  kind: source.kind,
  capabilities: source.capabilities,
  synthetic: source.synthetic ?? false,
  database_name: null,
}))

const jobs = new Map()
const byQuestion = new Map(questions.map((question) => [question.question.trim(), question.id]))
const statusOf = (job) => (Date.now() - job.started >= jobSeconds * 1000 ? 'success' : 'running')
/** The recorded turn, under the new job's id */
const turnOf = (job) => {
  const turn = session(job.session).turns[0]
  return JSON.parse(JSON.stringify(turn).replaceAll(turn.jobId, job.id))
}
const json = (res, status, body) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
const sse = (event, data, id) =>
  `${id ? `id: ${id}\n` : ''}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

createServer(async (req, res) => {
  const { pathname } = new URL(req.url, 'http://fake-deployment')
  if (req.method === 'GET' && pathname === '/v1/pack') return json(res, 200, PACK)
  if (req.method === 'GET' && pathname === '/v1/data_sources') return json(res, 200, SOURCES)
  if (req.method === 'POST' && pathname === '/v1/jobs/async/submit') {
    let body = ''
    for await (const chunk of req) body += chunk
    const request = JSON.parse(body)
    const id = request.job_id ?? `job-${jobs.size + 1}`
    const recorded = byQuestion.get(String(request.input).trim())
    if (!recorded)
      return json(res, 422, { detail: 'the stand-in knows only the recorded questions' })
    jobs.set(id, { id, session: recorded, started: Date.now() })
    return json(res, 200, { job_id: id, status: 'submitted' })
  }
  const match = pathname.match(/^\/v1\/jobs\/async\/job\/([^/]+)(\/[^/]+)?/)
  const job = match && jobs.get(match[1])
  if (!job) return json(res, 404, { detail: 'not found' })
  const route = `${req.method} ${match[2] ?? ''}`
  if (route === 'GET ')
    return json(res, 200, { job_id: job.id, status: statusOf(job), error: null })
  if (route === 'GET /export') return json(res, 200, { ...turnOf(job), status: statusOf(job) })
  if (route === 'POST /cancel')
    return json(res, 200, { job_id: job.id, status: 'interrupted', cancelled: true })
  if (route === 'GET /stream') {
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
