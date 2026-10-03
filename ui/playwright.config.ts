// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * End-to-end tests against the production build (`npm run build` first).
 *
 * The UI servers run from the same build: live mode against a fake API (with
 * the default pack's committed recordings), replay mode on the fixture data
 * pack, and replay mode on each pack's committed recordings.
 *
 * Two projects:
 * - `smoke` (`npm run e2e`): behavior, no screenshots.
 * - `visual` (`npm run e2e:visual`): screenshot baselines of the views that must keep the original
 *   demo UI's look, on the fixture pack and the fake API only. The baselines are rendered in the
 *   official Playwright Docker image, so the project runs only there (e2e/visual/README.md).
 */

import { existsSync, readdirSync } from 'node:fs'
import { defineConfig, devices } from '@playwright/test'

const FAKE_API = 'http://127.0.0.1:3990'
export const LIVE_URL = 'http://127.0.0.1:3991'
export const REPLAY_URL = 'http://127.0.0.1:3992'
/** Live mode on the fixture pack's recordings, for the visual baselines */
export const VISUAL_LIVE_URL = 'http://127.0.0.1:3989'
export const DATA_PACKS_DIR = `${process.cwd()}/../data/packs`
const FIXTURE_PACKS_DIR = `${process.cwd()}/e2e/fixtures/packs`

const packs = readdirSync(DATA_PACKS_DIR, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort()
const hasRecordings = (pack: string) =>
  existsSync(`${DATA_PACKS_DIR}/${pack}/recordings/index.json`)

/**
 * The data packs whose recordings bundle is in this checkout, each replayed on its own server.
 * A pack can ship without one (the public repository has no us-equities bundle).
 */
export const RECORDED_PACKS: Readonly<Record<string, string>> = Object.fromEntries(
  packs.filter(hasRecordings).map((pack, i) => [pack, `http://127.0.0.1:${3993 + i}`])
)
/** Packs without a recordings bundle, reported as skipped */
export const UNRECORDED_PACKS: readonly string[] = packs.filter((pack) => !hasRecordings(pack))

/** Set by e2e/visual/run.sh inside the Playwright image; the visual project runs only there. */
export const VISUAL = process.env.E2E_VISUAL === '1'

const uiServer = (url: string, env: Record<string, string>) => ({
  command: 'node .next/standalone/server.js',
  url: `${url}/api/health`,
  env: { HOSTNAME: '127.0.0.1', PORT: new URL(url).port, ...env },
  reuseExistingServer: !process.env.CI,
})

export default defineConfig({
  testDir: './e2e',
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  // One baseline per view, rendered on Linux in the Playwright image
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}{ext}',
  use: {
    trace: 'on-first-retry',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'smoke',
      testIgnore: 'visual/**',
      use: {
        ...devices['Desktop Chrome'],
        // A fake microphone (a tone), granted without a prompt, for the voice input test
        permissions: ['microphone'],
        launchOptions: {
          args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
        },
      },
    },
    {
      name: 'visual',
      testDir: './e2e/visual',
      // Its own output, the only one CI uploads: the fixture pack and the fake API, never a pack's recordings
      outputDir: './test-results/visual',
      use: {
        ...devices['Desktop Chrome'],
        // The size and theme of the parity screenshots taken against the original demo UI
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
        colorScheme: 'dark',
        locale: 'en-US',
        timezoneId: 'UTC',
      },
    },
  ],
  webServer: [
    {
      command: 'node e2e/fake-api.mjs',
      url: `${FAKE_API}/v1/pack`,
      env: { FAKE_API_PORT: new URL(FAKE_API).port },
      reuseExistingServer: !process.env.CI,
    },
    // Live mode also lists the default pack's committed recordings, as the stack's UI does
    uiServer(LIVE_URL, {
      UI_MODE: 'live',
      API_URL: FAKE_API,
      SPEECH_INPUT_ENABLED: 'true',
      PACKS_DIR: DATA_PACKS_DIR,
      DATA_PACK: 'synthetic-market',
    }),
    uiServer(REPLAY_URL, { UI_MODE: 'replay', PACKS_DIR: FIXTURE_PACKS_DIR, DATA_PACK: 'e2e' }),
    ...Object.entries(RECORDED_PACKS).map(([pack, url]) =>
      uiServer(url, { UI_MODE: 'replay', PACKS_DIR: DATA_PACKS_DIR, DATA_PACK: pack })
    ),
    // The baselines never depend on a re-recordable bundle
    ...(VISUAL
      ? [
          uiServer(VISUAL_LIVE_URL, {
            UI_MODE: 'live',
            API_URL: FAKE_API,
            SPEECH_INPUT_ENABLED: 'true',
            PACKS_DIR: FIXTURE_PACKS_DIR,
            DATA_PACK: 'e2e',
          }),
        ]
      : []),
  ],
})
