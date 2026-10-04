// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * A stand-in for the demo API, just enough for the live-mode smoke test: the packs (retail,
 * manufacturing and the workspace) with their views and data sources, a documents API whose files
 * move one pipeline stage per status poll, jobs whose stream answers at once with a cited report,
 * and a transcription of any WAV recording.
 * Usage: FAKE_API_PORT=3990 node e2e/fake-api.mjs
 */

import { createServer } from 'node:http'
import { createDocuments, readBody } from './fake-documents.mjs'

const question = (id, label, text, sources, tools, featured = false) => ({
  id,
  label,
  tag: null,
  description: null,
  question: text,
  sources,
  tools,
  featured,
})

const RETAIL_QUESTIONS = [
  question(
    'top-customers',
    'Top Customers',
    'Which customers spent the most last quarter?',
    ['retail.sales'],
    ['duckdb'],
    true
  ),
  // Featured questions at a realistic length, so the landing page is laid out with a full set of six
  question(
    'returns-policy',
    'Returns Policy',
    'What is the return window for opened electronics, which exceptions apply to gold-tier loyalty members, and what restocking fee does the policy charge when an item is not defective?',
    ['retail.policies'],
    ['retrieval'],
    true
  ),
  question(
    'tier-revenue',
    'Revenue by Tier',
    'Which loyalty tier brought the most net revenue in the third quarter of 2026, how many orders did each tier place, and how does that compare with the second quarter?',
    ['retail.sales'],
    ['duckdb'],
    true
  ),
  question(
    'churn-risk',
    'Churn Risk',
    'Which customers are the most likely to stop ordering in the next 90 days, and what did they buy before they slowed down?',
    ['retail.sales'],
    ['kumo', 'duckdb'],
    true
  ),
  question(
    'returns-and-revenue',
    'Returns and Revenue',
    'Which product categories had the most returns this quarter, and what does the returns policy say about the categories with the highest return rate?',
    ['retail.sales', 'retail.policies'],
    ['retrieval', 'duckdb'],
    true
  ),
  question(
    'store-sops',
    'Store Procedures',
    'What does the store operations manual say about handling a customer who returns an item without a receipt, and who may approve an exception?',
    ['retail.manuals'],
    ['retrieval'],
    true
  ),
  // More, so the composer's example picker has more than its five rows to show
  question(
    'basket-size',
    'Basket Size',
    'How did the average basket size change month by month?',
    ['retail.sales'],
    ['duckdb']
  ),
  question(
    'gold-orders',
    'Gold Orders',
    'How many orders did gold-tier customers place?',
    ['retail.sales'],
    ['ontology']
  ),
  question(
    'promo-lift',
    'Promotion Lift',
    'Which promotions lifted revenue the most?',
    ['retail.sales'],
    ['duckdb']
  ),
]

const PACKS = {
  retail: {
    id: 'retail',
    kind: 'industry',
    title: 'Retail',
    description: 'A fictional retailer (synthetic).',
    icon: 'Store',
    status: 'ready',
    version: '1.0.0',
    as_of: '2026-09-30',
    disclaimer: 'Synthetic data for tests.',
    questions: RETAIL_QUESTIONS,
    // The picker's questions, in its order: not promo-lift, and store-sops needs a source this API
    // does not offer
    examples: [
      'churn-risk',
      'basket-size',
      'returns-and-revenue',
      'gold-orders',
      'store-sops',
      'top-customers',
      'returns-policy',
      'tier-revenue',
    ],
    conversations: [],
  },
  manufacturing: {
    id: 'manufacturing',
    kind: 'industry',
    title: 'Manufacturing',
    description: 'A fictional plant (synthetic).',
    icon: 'Factory',
    status: 'ready',
    version: '1.0.0',
    as_of: '2026-09-30',
    disclaimer: 'Synthetic plant data for tests.',
    questions: [
      question(
        'press-lockout',
        'Press Lockout',
        'What must an operator do before clearing a jam on press line 3?',
        ['manufacturing.sops'],
        ['retrieval'],
        true
      ),
      question(
        'downtime',
        'Downtime by Line',
        'Which line had the most unplanned downtime in September?',
        ['manufacturing.maintenance'],
        ['duckdb'],
        true
      ),
      question(
        'failure-risk',
        'Failure Risk',
        'Which machines are most likely to fail in the next 30 days?',
        ['manufacturing.maintenance'],
        ['kumo'],
        false
      ),
    ],
    examples: ['press-lockout', 'downtime', 'failure-risk'],
    conversations: [],
  },
  workspace: {
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
  },
}

/**
 * Packs the selector does not list, for the states a deployment passes through: `uncataloged` (the
 * API has no catalog yet: a 503) and `syncing` (a pack ingest is still loading).
 */
const SYNCING = { ...PACKS.retail, id: 'syncing', status: 'ingesting' }
const CATALOG_503 = { detail: 'The knowledge catalog is not built yet.' }

const source = (id, name, kind, extra = {}) => ({
  id,
  pack_id: id.split('.')[0],
  name,
  description: `${name} (synthetic)`,
  kind,
  database_name: kind === 'structured' ? id.replace('.', '_') : null,
  capabilities: kind === 'structured' ? ['structured_retrieval'] : ['unstructured_retrieval'],
  synthetic: true,
  status: 'ready',
  ...extra,
})

const SOURCES = {
  retail: [
    source('retail.sales', 'Sales', 'structured'),
    source('retail.policies', 'Store policies', 'documents', { default_enabled: false }),
  ],
  manufacturing: [
    source('manufacturing.sops', 'Procedures', 'documents'),
    source('manufacturing.maintenance', 'Maintenance', 'structured'),
  ],
}

// ---------------------------------------------------------------- documents (Your data)

const documents = createDocuments()

// ---------------------------------------------------------------- jobs

const answerFor = (sources) => {
  const workspace = sources.some((id) => id.startsWith('workspace.'))
  const markdown = workspace
    ? 'Opened items may be returned within 30 days [1], and order O-1001 was the largest at $120 [2].\n\n' +
      '**References:**\n' +
      '- [1] Unstructured retrieval evidence — policy.pdf, p. 1 — evidence `ev-1` — invocation `call-1`\n' +
      '- [2] Structured query result — orders — evidence `ev-2` — invocation `call-2`'
    : 'Customer C2 spent the most last quarter [1].\n\n**References:**\n' +
      '- [1] Structured query result — retail sales — evidence `ev-1` — invocation `call-1`'
  return markdown
}

const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

/** What the fake transcribes every recording to. */
const TRANSCRIPT = 'Which customers spent the most last quarter?'

const submitted = new Map()

createServer(async (req, res) => {
  const url = new URL(req.url, 'http://fake-api')
  const { pathname } = url
  const route = `${req.method} ${pathname}`

  if (route === 'GET /v1/packs') {
    return json(res, {
      packs: Object.values(PACKS).map(({ id, kind, title, description, icon, status }) => ({
        id,
        kind,
        title,
        description,
        icon,
        status,
      })),
    })
  }
  if (route === 'GET /v1/pack') {
    const id = url.searchParams.get('id')
    if (id === 'uncataloged') return json(res, CATALOG_503, 503)
    if (id === 'syncing') return json(res, SYNCING)
    const pack = PACKS[id ?? 'retail']
    return pack ? json(res, pack) : json(res, { detail: 'Unknown pack' }, 404)
  }
  if (route === 'GET /v1/data_sources') {
    const pack = url.searchParams.get('pack')
    if (pack === 'workspace') return json(res, documents.sources())
    if (pack === 'uncataloged') return json(res, CATALOG_503, 503)
    if (pack === 'syncing') {
      return json(
        res,
        SOURCES.retail.map((entry) => ({ ...entry, pack_id: 'syncing', status: 'ingesting' }))
      )
    }
    return json(res, pack ? (SOURCES[pack] ?? []) : Object.values(SOURCES).flat())
  }

  if (await documents.handle(req, res, pathname)) return

  if (route === 'POST /v1/jobs/async/submit') {
    const request = JSON.parse((await readBody(req)).toString())
    submitted.set(request.job_id, request)
    return json(res, { job_id: request.job_id, status: 'submitted' })
  }
  if (route === 'POST /v1/speech/transcriptions') {
    const audio = await readBody(req)
    const wav = audio.length > 44 && audio.subarray(0, 4).toString() === 'RIFF'
    if (!wav || req.headers['content-type'] !== 'audio/wav') return res.writeHead(422).end()
    return json(res, { text: TRANSCRIPT })
  }
  const stream = pathname.match(/^\/v1\/jobs\/async\/job\/([^/]+)\/stream/)
  if (req.method === 'GET' && stream) {
    const request = submitted.get(stream[1]) ?? { data_sources: [] }
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(sse('job.status', { status: 'running' }))
    res.write(
      sse('artifact.update', {
        data: {
          type: 'output',
          output_category: 'final_report',
          content: answerFor(request.data_sources ?? []),
        },
      })
    )
    return res.end(sse('job.status', { status: 'success' }))
  }
  res.writeHead(404).end()
}).listen(Number(process.env.FAKE_API_PORT ?? 3990), '127.0.0.1')
