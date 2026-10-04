// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Your data's documents API, as the stand-in servers emulate it (e2e/fake-api.mjs for the smoke
 * test, e2e-live/fake-deployment.mjs for the live test): the `workspace` collection, uploads,
 * deletes, and an upload job's status, on which each file moves one pipeline stage per poll. A
 * document is read by Nemotron Parse 2.0, a table by DuckDB; once ready, they are the sources
 * `workspace.documents` and `workspace.tables`.
 *
 * For the failure paths: a file named `unreadable…` fails after its first stage, with a message, one
 * named `stuck…` stays at its first stage (so a screenshot can show a file mid-pipeline),
 * and `POST /__fake/ingest-down` makes the next look at the workspace collection answer 503, as the
 * API does while the ingest service is down.
 */

export const DOCUMENT_STAGES = ['received', 'parsing', 'chunking', 'embedding', 'indexing', 'ready']
export const TABLE_STAGES = ['received', 'loading', 'profiling', 'ready']
const TABLE_EXTENSIONS = /\.(csv|tsv|xlsx|xls|parquet|json|jsonl)$/i

/** A workspace source, in the shape of `GET /v1/data_sources` */
const workspaceSource = (id, name, kind) => ({
  id,
  pack_id: 'workspace',
  name,
  description: `${name} (synthetic)`,
  kind,
  database_name: kind === 'structured' ? id.replace('.', '_') : null,
  capabilities: kind === 'structured' ? ['structured_retrieval'] : ['unstructured_retrieval'],
  synthetic: true,
  status: 'ready',
})

const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

export const readBody = async (req) => {
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  return Buffer.concat(chunks)
}

/** One server's documents: its uploaded files and upload jobs, and the routes that serve them. */
export const createDocuments = () => {
  /** Uploaded files by id, and upload jobs by id */
  const files = new Map()
  const jobs = new Map()
  let sequence = 0
  /** How many looks at the collection still answer 503 (the ingest service down) */
  let downFor = 0

  const fileInfo = (file) => {
    const stages = file.kind === 'table' ? TABLE_STAGES : DOCUMENT_STAGES
    const failed = file.unreadable && file.step >= 2
    const stage = failed ? 'failed' : stages[Math.min(file.step, stages.length - 1)]
    const ready = stage === 'ready'
    if (failed) {
      return {
        ...fileInfo({ ...file, unreadable: false, step: 1 }),
        status: 'failed',
        stage,
        stage_detail: null,
        error_message:
          file.kind === 'table'
            ? 'DuckDB could not read this file as a table.'
            : 'Nemotron Parse could not read this file: it is not a valid PDF.',
      }
    }
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
        ready && file.kind === 'table'
          ? [file.name.replace(/\..*$/, '').replace(/\W+/g, '_')]
          : null,
      warnings: [],
      progress_percent: Math.round((100 * file.step) / (stages.length - 1)),
      error_message: null,
    }
  }

  /** Your documents once a document is ready, Your tables once a table is */
  const sources = () => {
    const ready = [...files.values()].map(fileInfo).filter((file) => file.status === 'success')
    return [
      ...(ready.some((file) => file.kind === 'document')
        ? [workspaceSource('workspace.documents', 'Your documents', 'documents')]
        : []),
      ...(ready.some((file) => file.kind === 'table')
        ? [workspaceSource('workspace.tables', 'Your tables', 'structured')]
        : []),
    ]
  }

  const collection = () => ({
    name: 'workspace',
    description: 'Your uploaded documents and tables',
    file_count: files.size,
    chunk_count: 0,
    backend: 'ingest',
    metadata: {},
  })

  /** Serves a documents route; false when the request is not one. */
  const handle = async (req, res, pathname) => {
    const route = `${req.method} ${pathname}`
    if (route === 'POST /__fake/ingest-down') {
      downFor = Number(new URL(req.url, 'http://fake').searchParams.get('times') ?? 1)
      return (json(res, { downFor }), true)
    }
    if (route === 'GET /v1/collections/workspace' && downFor > 0) {
      downFor -= 1
      return (json(res, { detail: 'The ingest service is unavailable.' }, 503), true)
    }
    if (route === 'GET /v1/collections/workspace') return (json(res, collection()), true)
    if (route === 'GET /v1/collections/workspace/documents') {
      return (json(res, { files: [...files.values()].map(fileInfo) }), true)
    }
    if (route === 'POST /v1/collections/workspace/documents') {
      if (!req.headers['content-type']?.startsWith('multipart/form-data; boundary=')) {
        return (json(res, { detail: 'Expected a multipart upload' }, 415), true)
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
          unreadable: /^unreadable/i.test(name),
          stuck: /^stuck/i.test(name),
        })
        return id
      })
      jobs.set(jobId, ids)
      json(res, { job_id: jobId, file_ids: ids, message: `${ids.length} files accepted` })
      return true
    }
    if (route === 'DELETE /v1/collections/workspace/documents') {
      const { file_ids: ids = [] } = JSON.parse((await readBody(req)).toString() || '{}')
      for (const id of ids) files.delete(id)
      return (json(res, { deleted: ids }), true)
    }
    const status = pathname.match(/^\/v1\/documents\/([^/]+)\/status$/)
    if (req.method === 'GET' && status) {
      const ids = jobs.get(status[1])
      if (!ids) return (json(res, { detail: 'Unknown job' }, 404), true)
      for (const id of ids) {
        const file = files.get(id)
        if (file) file.step = file.stuck ? 1 : file.step + 1
      }
      const details = ids.flatMap((id) => (files.has(id) ? [fileInfo(files.get(id))] : []))
      const done = details.every((file) => file.status === 'success' || file.status === 'failed')
      json(res, {
        job_id: status[1],
        status: done ? 'completed' : 'processing',
        submitted_at: '2026-10-02T10:00:00Z',
        total_files: details.length,
        processed_files: details.filter((file) => file.status !== 'ingesting').length,
        file_details: details,
        collection_name: 'workspace',
        backend: 'ingest',
        metadata: {},
      })
      return true
    }
    return false
  }

  return { handle, sources }
}
