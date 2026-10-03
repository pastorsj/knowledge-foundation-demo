// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The live end-to-end test against a running deployment (e2e-live/live.spec.ts), run by
 * `scripts/demo.sh test live --url URL`. No web server: LIVE_URL is the deployment's UI. Never run in CI, whose
 * Playwright suite is playwright.config.ts.
 */

import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e-live',
  testMatch: 'live.spec.ts',
  outputDir: './test-results/live',
  retries: 0,
  workers: 1,
  reporter: 'list',
  // The results table and live-results.json say what failed; no traces or screenshots of the deployment's data
  use: {
    ...devices['Desktop Chrome'],
    trace: 'off',
    screenshot: 'off',
    // A step that waits longer has failed; the jobs themselves are polled through the API
    actionTimeout: 60_000,
    navigationTimeout: 60_000,
  },
})
