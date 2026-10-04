// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Pack Client (server-side)
 *
 * Reads the packs a user can pick (`GET /v1/packs`, the generated `PackList`)
 * and one pack's public view (`GET /v1/pack?id=`, the generated `PackView`):
 * its title, disclaimer, demo questions and the examples of the composer's
 * picker. Used by server components, so it calls the API directly at
 * `API_URL` instead of going through the browser proxy. Replay mode has no
 * API: the same views come from each pack's replay bundle (`pack.json`).
 */

import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { z } from 'zod'
import { isPill } from '@/shared/components/ToolPills'
import type { PackSummary as GeneratedPackSummary } from '@/generated/packs'
import { isPackId, readApiUrl, readPacksDir, readRecordingsDir } from '@/shared/config/env'
import { orderPacks } from '@/shared/config/packs'

const PackQuestionSchema = z.object({
  id: z.string(),
  label: z.string(),
  /** The tools it is expected to use, as pills (the composer's demo scenario list shows them) */
  tools: z
    .array(z.string())
    .nullish()
    .transform((tools) => (tools ?? []).filter(isPill)),
  description: z.string().nullish(),
  question: z.string(),
  sources: z.array(z.string()),
  featured: z.boolean().default(false),
})

const PackKindSchema = z.enum(['industry', 'workspace'])
const PackStatusSchema = z.enum(['ready', 'ingesting', 'failed', 'empty'])

/** `PackView` (`@/generated/pack`) as the UI reads it; its spec checks that it accepts every view. */
export const PackSchema = z.object({
  id: z.string(),
  kind: PackKindSchema.default('industry'),
  title: z.string(),
  icon: z.string().nullish(),
  status: PackStatusSchema.default('ready'),
  disclaimer: z.string().nullish(),
  questions: z.array(PackQuestionSchema),
  /** The ids of the questions the composer's example picker offers, in order (none from an older API) */
  examples: z.array(z.string()).nullish(),
})

/** `PackSummary` (`@/generated/packs`): one entry of the industry selector. */
export const PackSummarySchema = z.object({
  id: z.string(),
  kind: PackKindSchema.default('industry'),
  title: z.string(),
  description: z
    .string()
    .nullish()
    .transform((value) => value ?? null),
  icon: z
    .string()
    .nullish()
    .transform((value) => value ?? null),
  status: PackStatusSchema.default('ready'),
})

export const PackListSchema = z.object({ packs: z.array(PackSummarySchema) })

// The schema parses to the generated type, so the two cannot drift apart unnoticed
const _parsesToGenerated = (parsed: z.infer<typeof PackSummarySchema>): GeneratedPackSummary =>
  parsed
void _parsesToGenerated

export type PackQuestion = z.infer<typeof PackQuestionSchema>
export type Pack = z.infer<typeof PackSchema>
/** The generated `PackSummary`, which PackSummarySchema parses to */
export type PackSummary = GeneratedPackSummary

const getJson = async (url: string): Promise<unknown> => {
  const response = await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(3000) })
  if (!response.ok) throw new Error(`${url} returned ${response.status}`)
  return response.json()
}

/** One pack's view (the API's default pack without an id), or null when unavailable or malformed. */
export const fetchPack = async (packId?: string | null): Promise<Pack | null> => {
  try {
    const query = packId ? `?${new URLSearchParams({ id: packId })}` : ''
    const parsed = PackSchema.safeParse(await getJson(`${readApiUrl()}/v1/pack${query}`))
    return parsed.success ? parsed.data : null
  } catch {
    return null
  }
}

/** The packs a user can pick; empty when the API is unavailable. */
export const fetchPacks = async (): Promise<PackSummary[]> => {
  try {
    const parsed = PackListSchema.safeParse(await getJson(`${readApiUrl()}/v1/packs`))
    return parsed.success ? parsed.data.packs : []
  } catch {
    return []
  }
}

/** Replay: a pack's view from its bundle (`pack.json`), or null when it has none. */
export const readRecordedPack = async (packId: string): Promise<Pack | null> => {
  try {
    const file = path.join(readRecordingsDir(packId), 'pack.json')
    const parsed = PackSchema.safeParse(JSON.parse(await readFile(file, 'utf8')))
    return parsed.success ? parsed.data : null
  } catch {
    return null
  }
}

/** Replay: the packs with a replay bundle, as `GET /v1/packs` orders them (industries by title, then the workspace). */
export const readRecordedPacks = async (): Promise<PackSummary[]> => {
  let ids: string[]
  try {
    ids = (await readdir(readPacksDir(), { withFileTypes: true }))
      .filter((entry) => entry.isDirectory() && isPackId(entry.name))
      .map((entry) => entry.name)
  } catch {
    return []
  }
  const packs = await Promise.all(
    ids.map(async (id): Promise<PackSummary[]> => {
      try {
        const file = path.join(readRecordingsDir(id), 'pack.json')
        const view = JSON.parse(await readFile(file, 'utf8')) as Record<string, unknown>
        const parsed = PackSummarySchema.safeParse({ ...view, id, status: 'ready' })
        return parsed.success ? [parsed.data] : []
      } catch {
        return []
      }
    })
  )
  return orderPacks(packs.flat())
}
