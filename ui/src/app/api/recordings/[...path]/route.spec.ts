// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { mkdir, mkdtemp, rm, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterAll, beforeAll, describe, expect, test, vi } from 'vitest'
import { GET } from './route'

const get = (...segments: string[]) =>
  GET(new Request('http://ui.test/api/recordings'), {
    params: Promise.resolve({ path: segments }),
  })

describe('/api/recordings', () => {
  let packsDir: string

  beforeAll(async () => {
    packsDir = await mkdtemp(path.join(tmpdir(), 'packs-'))
    const recordings = path.join(packsDir, 'retail', 'recordings')
    await mkdir(path.join(recordings, 'sessions'), { recursive: true })
    await writeFile(path.join(recordings, 'index.json'), '{"formatVersion":2}')
    await writeFile(path.join(recordings, 'sessions', 'events.jsonl'), '{}\n{}\n')
    await writeFile(path.join(recordings, 'notes.txt'), 'not served')
    await writeFile(
      path.join(recordings, 'pack.json'),
      JSON.stringify({ id: 'retail', kind: 'industry', title: 'Retail', icon: 'Store' })
    )
    const workspace = path.join(packsDir, 'workspace', 'recordings')
    await mkdir(workspace, { recursive: true })
    await writeFile(
      path.join(workspace, 'pack.json'),
      JSON.stringify({ id: 'workspace', kind: 'workspace', title: 'Your data', icon: 'Upload' })
    )
    const banking = path.join(packsDir, 'financial-services', 'recordings')
    await mkdir(banking, { recursive: true })
    await writeFile(
      path.join(banking, 'pack.json'),
      JSON.stringify({ id: 'financial-services', title: 'Financial Services', icon: 'Bank' })
    )
    // A directory without a bundle is not a recorded pack
    await mkdir(path.join(packsDir, 'healthcare'))
    await writeFile(path.join(packsDir, 'secret.json'), '{}')
    await symlink(path.join(packsDir, 'secret.json'), path.join(recordings, 'escape.json'))
    vi.stubEnv('PACKS_DIR', packsDir)
  })

  afterAll(async () => {
    vi.unstubAllEnvs()
    await rm(packsDir, { recursive: true, force: true })
  })

  test('serves JSON and JSON Lines files of a pack’s bundle', async () => {
    const index = await get('retail', 'index.json')
    expect(index.headers.get('content-type')).toBe('application/json')
    expect(await index.json()).toEqual({ formatVersion: 2 })

    const events = await get('retail', 'sessions', 'events.jsonl')
    expect(events.headers.get('content-type')).toBe('application/x-ndjson')
    expect(await events.text()).toBe('{}\n{}\n')
  })

  test('lists the packs with a bundle: industries by title, then the workspace', async () => {
    expect(await (await get('packs.json')).json()).toEqual({
      packs: [
        {
          id: 'financial-services',
          kind: 'industry',
          title: 'Financial Services',
          description: null,
          icon: 'Bank',
          status: 'ready',
        },
        {
          id: 'retail',
          kind: 'industry',
          title: 'Retail',
          description: null,
          icon: 'Store',
          status: 'ready',
        },
        {
          id: 'workspace',
          kind: 'workspace',
          title: 'Your data',
          description: null,
          icon: 'Upload',
          status: 'ready',
        },
      ],
    })
  })

  test.each([
    [['retail', 'missing.json']],
    [['retail', 'notes.txt']],
    [['retail', '..', 'secret.json']],
    [['retail', 'escape.json']],
    [['..', 'secret.json']],
    [['Retail', 'index.json']],
    [['index.json']],
    [['healthcare', 'index.json']],
  ])('does not serve %j', async (segments) => {
    expect((await get(...segments)).status).toBe(404)
  })
})

describe('/api/recordings/index.json of a bundle recorded before sessions listed their tools', () => {
  let packsDir: string
  const recorded = { pill: 'retrieval', tools: ['retrieve_evidence'] }

  beforeAll(async () => {
    packsDir = await mkdtemp(path.join(tmpdir(), 'packs-'))
    const recordings = path.join(packsDir, 'older', 'recordings')
    await mkdir(path.join(recordings, 'sessions'), { recursive: true })
    const sessions = [
      { id: 'policies', title: 'Policies', turns: [], tools: [recorded] },
      { id: 'revenue', title: 'Revenue', turns: [] },
    ]
    await writeFile(path.join(recordings, 'index.json'), JSON.stringify({ sessions }))
    const turn = {
      events: [
        { eventKind: 'tool.completed', toolName: 'skill_view' },
        {
          eventKind: 'artifact.available',
          toolName: 'query_tables',
          artifactRefs: ['r1'],
        },
      ],
      receipts: [{ receiptId: 'r1', content: {} }],
    }
    await writeFile(
      path.join(recordings, 'sessions', 'revenue.json'),
      JSON.stringify({ turns: [turn] })
    )
    vi.stubEnv('PACKS_DIR', packsDir)
  })

  afterAll(async () => {
    vi.unstubAllEnvs()
    await rm(packsDir, { recursive: true, force: true })
  })

  test('derives the missing tools from the recorded events', async () => {
    const index = await (await get('older', 'index.json')).json()

    expect(index.sessions[0].tools).toEqual([recorded])
    expect(index.sessions[1].tools).toEqual([{ pill: 'duckdb', tools: ['query_tables'] }])
  })
})
