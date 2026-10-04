// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Server-side runtime configuration.
 *
 * Read at request time so one image serves any environment. Invalid values
 * fail loudly instead of silently falling back.
 *
 * | Variable     | Default               | Meaning                                         |
 * |--------------|-----------------------|-------------------------------------------------|
 * | `UI_MODE`    | `live`                | `live` talks to the API; `replay` never does     |
 * | `API_URL`    | `http://api:8000`     | Base URL of the demo API (server-side only)      |
 * | `PACKS_DIR`  | `/packs`              | Directory of the packs (their replay bundles)    |
 * | `DEFAULT_PACK` | `retail`            | The pack shown until the user picks another      |
 * | `PHOENIX_URL`| unset                 | Browser-reachable Phoenix UI; unset hides links  |
 * | `SPEECH_INPUT_ENABLED` | `false`     | Show the microphone (live mode; the API transcribes) |
 * | `SPEECH_INPUT_MAX_SECONDS` | `60`    | Longest recording, 1 to 90 seconds               |
 */

import path from 'node:path'
import type { AppConfig, UiMode } from '@/shared/context'
import { getFileUploadConfigFromEnv } from './file-upload'

type Env = Record<string, string | undefined>

/** A pack id, as the API's catalog has them (`retail`, `financial-services`, `workspace`). */
export const PACK_ID = /^[a-z][a-z0-9-]{0,63}$/

/** Whether a value is a well-formed pack id. */
export const isPackId = (value: unknown): value is string =>
  typeof value === 'string' && PACK_ID.test(value)

const readMode = (env: Env): UiMode => {
  const value = env.UI_MODE?.trim() || 'live'
  if (value !== 'live' && value !== 'replay') {
    throw new Error(`UI_MODE must be "live" or "replay", got "${value}"`)
  }
  return value
}

const readHttpUrl = (name: string, value: string): string => {
  const url = new URL(value)
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`${name} must be an http(s) URL`)
  }
  return value.replace(/\/+$/, '')
}

const readMaxSeconds = (env: Env): number => {
  const raw = env.SPEECH_INPUT_MAX_SECONDS?.trim()
  if (!raw || !/^\d+$/.test(raw)) return 60
  return Math.min(90, Math.max(1, Number(raw)))
}

/** Configuration the browser needs, passed through `AppConfigProvider`. */
export const readAppConfig = (env: Env = process.env): AppConfig => {
  const phoenixUrl = env.PHOENIX_URL?.trim()
  const mode = readMode(env)
  return {
    mode,
    phoenixUrl: phoenixUrl ? readHttpUrl('PHOENIX_URL', phoenixUrl) : null,
    speechInput: {
      // Replay never calls the API, so it has no transcription
      enabled:
        mode === 'live' &&
        ['true', '1', 'yes', 'on'].includes(env.SPEECH_INPUT_ENABLED?.trim().toLowerCase() ?? ''),
      maxSeconds: readMaxSeconds(env),
    },
    fileUpload: getFileUploadConfigFromEnv(env as NodeJS.ProcessEnv),
  }
}

/** FILE_UPLOAD_MAX_REQUEST_MB: the largest upload request the proxy forwards (default 512 MB). */
export const readMaxUploadRequestBytes = (env: Env = process.env): number => {
  const raw = Number(env.FILE_UPLOAD_MAX_REQUEST_MB?.trim() || 512)
  return (Number.isFinite(raw) && raw > 0 ? raw : 512) * 1024 * 1024
}

export const readUiMode = (env: Env = process.env): UiMode => readMode(env)

export const readApiUrl = (env: Env = process.env): string =>
  readHttpUrl('API_URL', env.API_URL?.trim() || 'http://api:8000')

/** `DEFAULT_PACK`: the pack shown until the user picks another (`?pack=`, cookie `kf-pack`). */
export const readDefaultPack = (env: Env = process.env): string => {
  const pack = env.DEFAULT_PACK?.trim() || 'retail'
  if (!isPackId(pack)) throw new Error(`DEFAULT_PACK must match ${PACK_ID}, got "${pack}"`)
  return pack
}

/** `$PACKS_DIR`: the directory of the packs, each with its replay bundle in `<pack>/recordings`. */
export const readPacksDir = (env: Env = process.env): string =>
  path.resolve(env.PACKS_DIR?.trim() || '/packs')

/** `$PACKS_DIR/<pack>/recordings`: a pack's replay bundle. */
export const readRecordingsDir = (pack: string, env: Env = process.env): string => {
  if (!isPackId(pack)) throw new Error(`A pack id must match ${PACK_ID}, got "${pack}"`)
  return path.join(readPacksDir(env), pack, 'recordings')
}
