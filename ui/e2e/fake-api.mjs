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

const DOCUMENT_STAGES = ['received', 'parsing', 'chunking', 'embedding', 'indexing', 'ready']
const TABLE_STAGES = ['received', 'loading', 'profiling', 'ready']
const TABLE_EXTENSIONS = /\.(csv|tsv|xlsx|xls|parquet|json|jsonl)$/i

/** Uploaded files by id, and jobs by id; a file moves one stage per poll of its job's status */
const files = new Map()
const jobs = new Map()
let sequence = 0

const fileInfo = (file) => {
  const stages = file.kind === 'table' ? TABLE_STAGES : DOCUMENT_STAGES
  const stage = stages[Math.min(file.step, stages.length - 1)]
  const ready = stage === 'ready'
  return {
    file_id: file.id,
    file_name: file.name,
    collection_name: 'workspace',
    status: ready ? 'success' : 'ingesting',
    file_size: file.size,
    chunk_count: ready && file.kind === 'document' ? 4 : 0,
    uploaded_at: '2026-10-02T10:00:00Z',
    metadata: {},
    kind: file.kind,
    stage,
    stage_detail: stage === 'parsing' ? 'page 1 of 1' : null,
    parser: file.kind === 'table' ? 'duckdb-csv' : 'nemotron-parse-2.0',
    tables:
      ready && file.kind === 'table' ? [file.name.replace(/\..*$/, '').replace(/\W+/g, '_')] : null,
    warnings: [],
    progress_percent: Math.round((100 * file.step) / (stages.length - 1)),
    error_message: null,
  }
}

const workspaceSources = () => {
  const ready = [...files.values()].map(fileInfo).filter((file) => file.status === 'success')
  return [
    ...(ready.some((file) => file.kind === 'document')
      ? [source('workspace.documents', 'Your documents', 'documents')]
      : []),
    // A structured source is offered once it has a table
    ...(ready.some((file) => file.kind === 'table')
      ? [source('workspace.tables', 'Your tables', 'structured')]
      : []),
  ]
}

const COLLECTION = () => ({
  name: 'workspace',
  description: 'Your uploaded documents and tables',
  file_count: files.size,
  chunk_count: 0,
  backend: 'ingest',
  metadata: {},
})

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

const readBody = async (req) => {
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  return Buffer.concat(chunks)
}

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
    const pack = PACKS[url.searchParams.get('id') ?? 'retail']
    return pack ? json(res, pack) : json(res, { detail: 'Unknown pack' }, 404)
  }
  if (route === 'GET /v1/data_sources') {
    const pack = url.searchParams.get('pack')
    if (pack === 'workspace') return json(res, workspaceSources())
    return json(res, pack ? (SOURCES[pack] ?? []) : Object.values(SOURCES).flat())
  }

  if (route === 'GET /v1/collections/workspace') return json(res, COLLECTION())
  if (route === 'POST /v1/collections') return json(res, COLLECTION())
  if (route === 'GET /v1/collections/workspace/documents') {
    return json(res, { files: [...files.values()].map(fileInfo) })
  }
  if (route === 'POST /v1/collections/workspace/documents') {
    if (!req.headers['content-type']?.startsWith('multipart/form-data; boundary=')) {
      return json(res, { detail: 'Expected a multipart upload' }, 415)
    }
    const body = (await readBody(req)).toString('latin1')
    const names = [...body.matchAll(/filename="([^"]+)"/g)].map((match) => match[1])
    const jobId = `job-upload-${++sequence}`
    const ids = names.map((name, index) => {
      const id = `file-${sequence}-${index}`
      files.set(id, {
        id,
        name,
        size: 1024,
        kind: TABLE_EXTENSIONS.test(name) ? 'table' : 'document',
        step: 0,
      })
      return id
    })
    jobs.set(jobId, ids)
    return json(res, { job_id: jobId, file_ids: ids, message: `${ids.length} files accepted` })
  }
  if (route === 'DELETE /v1/collections/workspace/documents') {
    const { file_ids: ids = [] } = JSON.parse((await readBody(req)).toString() || '{}')
    for (const id of ids) files.delete(id)
    return json(res, { deleted: ids })
  }
  const status = pathname.match(/^\/v1\/documents\/([^/]+)\/status$/)
  if (req.method === 'GET' && status) {
    const ids = jobs.get(status[1])
    if (!ids) return json(res, { detail: 'Unknown job' }, 404)
    for (const id of ids) {
      const file = files.get(id)
      if (file) file.step += 1
    }
    const details = ids.flatMap((id) => (files.has(id) ? [fileInfo(files.get(id))] : []))
    const done = details.every((file) => file.status === 'success')
    return json(res, {
      job_id: status[1],
      status: done ? 'completed' : 'processing',
      submitted_at: '2026-10-02T10:00:00Z',
      total_files: details.length,
      processed_files: details.filter((file) => file.status === 'success').length,
      file_details: details,
      collection_name: 'workspace',
      backend: 'ingest',
      metadata: {},
    })
  }

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
