// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Screenshot baselines of the views that keep the original demo UI's look: the landing page, a new
 * research session with its Data Sources panel, the question picker and the replays list with
 * their tool pills, a recorded answer, the execution graph, the explorers (DuckDB SQL, retrieval,
 * Auto Ontology SQL, Kumo), the Agent Activity panel (Thinking, Timeline), the data viewer and Your
 * data's Files tab.
 *
 * The data is fixed: the fixture packs (e2e/fixtures/packs, synthetic) and the fake API, never a
 * pack's recordings, which are re-recorded. Time and Math.random are frozen, CSS animations are
 * finished by toHaveScreenshot, and the starfield canvas is hidden (screenshot.css).
 * How to update the baselines: e2e/visual/README.md.
 */

import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { REPLAY_URL, VISUAL, VISUAL_LIVE_URL } from '../../playwright.config'

test.skip(!VISUAL, 'Baselines are rendered in the Playwright Docker image: e2e/visual/run.sh')

/** The clock every page sees */
const NOW = new Date('2026-10-01T12:00:00Z')
const SCREENSHOT_CSS = join(__dirname, 'screenshot.css')

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(NOW)
  // A seeded generator instead of Math.random (mulberry32)
  await page.addInitScript(() => {
    let seed = 0x5eed
    Math.random = () => {
      seed = (seed + 0x6d2b79f5) | 0
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296
    }
  })
})

/** Waits for the web fonts, the CDN icons (inlined by svg-loader) and the images. */
const settle = async (page: Page) => {
  await page.evaluate(() => document.fonts.ready)
  await expect
    .poll(
      () => page.evaluate(() => document.querySelectorAll('svg[data-src]:not([data-id])').length),
      { message: 'every CDN icon is inlined' }
    )
    .toBe(0)
  await expect
    .poll(
      () =>
        page.evaluate(() =>
          Array.from(document.images).every((image) => image.complete && image.naturalWidth > 0)
        ),
      { message: 'every image is loaded' }
    )
    .toBe(true)
  await page.evaluate(() => document.fonts.ready)
  // The pointer rests on the empty middle of the app bar, so no hover state or tooltip shows
  await page.mouse.move(720, 28)
}

const matchesBaseline = async (target: Page | Locator, name: string) => {
  const page = 'page' in target ? target.page() : target
  await settle(page)
  await expect(target).toHaveScreenshot(`${name}.png`, { stylePath: SCREENSHOT_CSS })
}

const openRecording = async (page: Page, title: string) => {
  await page.goto(`${REPLAY_URL}/research?pack=retail`)
  await page
    .getByRole('button', { name: `Recorded session: ${title}; Completed` })
    .first()
    .click()
  await expect(page.locator('.agent-final-response').first()).toBeVisible()
}

/** Opens the explorer of an answer's cited source, as the Sources list's Inspect does. */
const openCitedSource = async (page: Page, answer: number, source: number) => {
  const item = page
    .locator('.agent-final-response')
    .nth(answer)
    .getByRole('region', { name: 'Sources' })
    .getByRole('listitem')
    .nth(source)
  await item.locator('summary').click()
  await item.getByRole('button', { name: 'Open this run in the execution view' }).click()
}

const workspace = (page: Page) => page.getByRole('region', { name: 'Execution workspace' })

test.describe('live mode', () => {
  for (const colorScheme of ['dark', 'light'] as const) {
    test(`landing page (${colorScheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme })
      // The server renders the featured questions only if the API answers within 3 s
      await expect(async () => {
        await page.goto(`${VISUAL_LIVE_URL}/?pack=retail`)
        await expect(
          page.getByRole('region', { name: 'Featured questions' }).getByRole('link')
        ).toHaveCount(6, { timeout: 1_000 })
      }).toPass()
      await expect(page.locator('main [data-brand] img')).toHaveCount(12)
      await matchesBaseline(page, `landing-${colorScheme}`)
    })
  }

  test('new research session with the Data Sources panel', async ({ page }) => {
    await page.goto(`${VISUAL_LIVE_URL}/research?pack=retail`)
    await expect(page.getByRole('button', { name: 'Close data sources panel' })).toBeVisible()
    await expect(page.getByText('Sales', { exact: true })).toBeVisible()
    await matchesBaseline(page, 'research-data-sources')
  })

  test('question picker with tool pills', async ({ page }) => {
    await page.goto(`${VISUAL_LIVE_URL}/research?pack=retail`)
    // As the parity screenshots against the original were taken: with the Data Sources panel closed
    await page.getByRole('button', { name: 'Close data sources panel' }).click()
    await page.getByTestId('demo-scenario-select').click()
    // Seven examples: five rows show, the others scroll underneath
    await expect(page.getByRole('option')).toHaveCount(7)
    await expect(page.getByRole('option').locator('.tool-pill').first()).toBeVisible()
    await expect
      .poll(() => page.getByTestId('demo-scenario-list').evaluate((list) => list.style.maxHeight))
      .toMatch(/^min\(/)
    await matchesBaseline(page, 'question-picker')
  })

  test('Your data: Files tab with a file mid-pipeline, one ready and one failed', async ({
    page,
  }) => {
    await page.goto(`${VISUAL_LIVE_URL}/research?pack=workspace`)
    await expect(page.getByRole('radio', { name: 'Files' })).toBeChecked()
    // The fake pipeline keeps a `stuck…` file at its first stage and fails an `unreadable…` one
    const pdf = readFileSync(join(__dirname, '..', 'fixtures', 'files', 'policy.pdf'))
    const file = (name: string) => ({ name, mimeType: 'application/pdf', buffer: pdf })
    await page
      .getByTestId('composer-file-input')
      .setInputFiles([file('policy.pdf'), file('stuck-scan.pdf'), file('unreadable-memo.pdf')])
    const card = (name: string) => page.getByTestId('file-source-card').filter({ hasText: name })
    await expect(card('policy.pdf')).toHaveAttribute('data-status', 'available', {
      timeout: 30_000,
    })
    await expect(card('unreadable-memo.pdf')).toHaveAttribute('data-status', 'error')
    await expect(card('stuck-scan.pdf')).toHaveAttribute('data-stage', 'parsing')
    await matchesBaseline(page, 'your-data-files')
  })
})

test.describe('replay mode', () => {
  test('recorded sessions list with tool pills', async ({ page }) => {
    await page.goto(`${REPLAY_URL}/research?pack=retail`)
    const sessions = page.getByRole('button', { name: /^Recorded session: / })
    await expect(sessions).toHaveCount(2)
    await expect(sessions.locator('.tool-pill')).toHaveCount(4)
    await matchesBaseline(page, 'recorded-list')
  })

  test('recorded answer with its sources', async ({ page }) => {
    await openRecording(page, 'Returns policy and loyalty revenue')
    await expect(page.getByRole('region', { name: 'Sources' }).getByRole('listitem')).toHaveCount(2)
    await matchesBaseline(page, 'recorded-answer')
  })

  test('execution graph', async ({ page }) => {
    await openRecording(page, 'Returns policy and loyalty revenue')
    await page.getByRole('button', { name: 'View execution for this response' }).click()
    await expect(workspace(page).getByText('Step 13 of 13')).toBeVisible()
    await matchesBaseline(page, 'execution-graph')
  })

  test('DuckDB SQL explorer', async ({ page }) => {
    await openRecording(page, 'Returns policy and loyalty revenue')
    await openCitedSource(page, 0, 1)
    await expect(
      page.getByRole('dialog', { name: 'Structured Retrieval execution details' })
    ).toBeVisible()
    await matchesBaseline(page, 'explorer-duckdb-sql')
  })

  test('retrieval explorer', async ({ page }) => {
    await openRecording(page, 'Returns policy and loyalty revenue')
    await openCitedSource(page, 0, 0)
    await expect(
      page.getByRole('dialog', { name: 'Unstructured Retrieval execution details' })
    ).toBeVisible()
    await matchesBaseline(page, 'explorer-retrieval')
  })

  test('Auto Ontology text-to-SQL explorer', async ({ page }) => {
    await openRecording(page, 'Gold-tier orders and churn risk')
    await openCitedSource(page, 0, 0)
    const explorer = page.getByRole('dialog', { name: 'Auto Ontology text-to-SQL details' })
    await expect(explorer.getByText(/SELECT count/)).toBeVisible()
    await matchesBaseline(page, 'explorer-auto-ontology-sql')
  })

  test('Kumo explorer', async ({ page }) => {
    await openRecording(page, 'Gold-tier orders and churn risk')
    await openCitedSource(page, 1, 0)
    const explorer = page.getByRole('dialog', { name: 'NVIDIA Kumo execution details' })
    await expect(explorer.getByText(/PREDICT COUNT\(orders/)).toBeVisible()
    await matchesBaseline(page, 'explorer-kumo')
  })

  test('Agent Activity panel: thinking and timeline', async ({ page }) => {
    await openRecording(page, 'Returns policy and loyalty revenue')
    await page.getByRole('button', { name: 'Open agent activity panel' }).click()
    await expect(
      page.getByRole('list', { name: 'Hermes thinking activity' }).getByText('Answer ready')
    ).toBeVisible()
    await matchesBaseline(page, 'activity-thinking')

    await page.getByRole('tab', { name: 'Timeline' }).click()
    await expect(page.getByRole('region', { name: 'Execution action timeline' })).toBeVisible()
    await matchesBaseline(page, 'activity-timeline')
  })

  test('data viewer: a table and a SQL query', async ({ page }) => {
    await openRecording(page, 'Gold-tier orders and churn risk')
    await page.getByRole('button', { name: 'View execution for this response' }).first().click()
    await workspace(page).getByRole('button', { name: 'Inspect DuckDB Tables' }).click()
    const browser = workspace(page).getByRole('dialog', { name: 'Structured Database browser' })
    await browser.getByRole('button', { name: /customers/ }).click()
    await expect(browser.getByText('Ada Park')).toBeVisible()
    await matchesBaseline(page, 'data-viewer-table')

    await browser.getByRole('button', { name: 'SQL Query' }).click()
    await browser.getByRole('button', { name: 'Run query' }).click()
    await expect(browser.getByRole('region', { name: 'SQL results' })).toContainText('Cy Moreau')
    await matchesBaseline(page, 'data-viewer-sql')
  })
})
