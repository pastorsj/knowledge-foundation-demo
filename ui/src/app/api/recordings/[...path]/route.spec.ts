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
    await writeFile(path.join(packsDir, 'secret.json'), '{}')
    await symlink(path.join(packsDir, 'secret.json'), path.join(recordings, 'escape.json'))
    vi.stubEnv('PACKS_DIR', packsDir)
    vi.stubEnv('DATA_PACK', 'retail')
  })

  afterAll(async () => {
    vi.unstubAllEnvs()
    await rm(packsDir, { recursive: true, force: true })
  })

  test('serves JSON and JSON Lines files of the active pack', async () => {
    const index = await get('index.json')
    expect(index.headers.get('content-type')).toBe('application/json')
    expect(await index.json()).toEqual({ formatVersion: 2 })

    const events = await get('sessions', 'events.jsonl')
    expect(events.headers.get('content-type')).toBe('application/x-ndjson')
    expect(await events.text()).toBe('{}\n{}\n')
  })

  test.each([[['missing.json']], [['notes.txt']], [['..', 'secret.json']], [['escape.json']]])(
    'does not serve %j',
    async (segments) => {
      expect((await get(...segments)).status).toBe(404)
    }
  )
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
    vi.stubEnv('DATA_PACK', 'older')
  })

  afterAll(async () => {
    vi.unstubAllEnvs()
    await rm(packsDir, { recursive: true, force: true })
  })

  test('derives the missing tools from the recorded events', async () => {
    const index = await (await get('index.json')).json()

    expect(index.sessions[0].tools).toEqual([recorded])
    expect(index.sessions[1].tools).toEqual([{ pill: 'duckdb', tools: ['query_tables'] }])
  })
})
