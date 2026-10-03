// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The execution view in replay mode, on the synthetic v2 bundles in
 * e2e/fixtures/packs/<pack>/recordings (built from contracts/fixtures).
 */

import { expect, test, type Page } from '@playwright/test'
import { REPLAY_URL } from '../playwright.config'

test.use({ baseURL: REPLAY_URL })

const openSession = async (page: Page, title: string, pack = 'retail') => {
  await page.goto(`/research?pack=${pack}`)
  await page
    .getByRole('button', { name: `Recorded session: ${title}; Completed` })
    .first()
    .click()
}

test('the replays list shows the tools each recorded run used', async ({ page }) => {
  await page.goto('/research?pack=retail')
  const pills = (title: string) =>
    page
      .getByRole('button', { name: `Recorded session: ${title}; Completed` })
      .locator('.tool-pill')

  // Listed in the bundle's index, as `demo-api record` writes them
  await expect(pills('Returns policy and loyalty revenue')).toHaveText(['Retrieval', 'DuckDB'])
  // Derived from the recorded events, for an index that lacks them
  await expect(pills('Gold-tier orders and churn risk')).toHaveText(['Kumo', 'Ontology'])
  await expect(pills('Returns policy and loyalty revenue').nth(1)).toHaveAttribute(
    'data-family',
    'partner'
  )
})

test('a recorded session replays its run as a graph and opens an explorer', async ({ page }) => {
  const apiCalls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/')) apiCalls.push(request.url())
  })

  await openSession(page, 'Returns policy and loyalty revenue')
  await expect(page.getByText('Opened electronics can be returned within 30 days')).toBeVisible()
  await page.getByRole('button', { name: 'View execution for this response' }).click()

  const workspace = page.getByRole('region', { name: 'Execution workspace' })
  await expect(workspace.getByRole('heading', { name: 'Execution Graph' })).toBeVisible()
  await expect(workspace.getByText('Hermes Recorded')).toBeVisible()
  await expect(workspace.getByText('Step 13 of 13')).toBeVisible()
  const summary = workspace.getByRole('region', { name: 'Hermes run summary' })
  await expect(summary.getByText('2 tool call(s) ·', { exact: false })).toBeVisible()
  await expect(workspace.locator('[data-group-id]')).toHaveCount(4)

  // The documents path: Nemotron Parse, Embed, Milvus and, as the receipt names a rerank model, Rerank
  for (const id of [
    'retriever-tool',
    'nemotron-parse',
    'nemotron-embed',
    'milvus',
    'nemotron-rerank',
  ]) {
    await expect(workspace.locator(`[data-node-id="${id}"]`)).toHaveAttribute(
      'data-state',
      'completed'
    )
  }
  await expect(workspace.locator('[data-edge-id="milvus-evidence"]')).toHaveAttribute(
    'data-state',
    'unobserved'
  )
  const langchain = workspace.locator('[data-node-id="retriever-tool"]').getByRole('img', {
    name: 'LangChain',
  })
  await expect
    .poll(() => langchain.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBeGreaterThan(0)

  // The table query ran on DuckDB; its node opens the SQL and its rows
  await expect(workspace.locator('[data-node-id="tables-tool"]')).toHaveAttribute(
    'data-state',
    'completed'
  )
  await workspace.getByRole('button', { name: 'Inspect Structured Retrieval' }).click()
  const explorer = workspace.getByRole('dialog', {
    name: 'Structured Retrieval execution details',
  })
  await expect(explorer.getByRole('heading', { name: 'DuckDB Table Query' })).toBeVisible()
  await expect(explorer.getByText(/SELECT c\.tier/)).toBeVisible()
  await explorer
    .getByRole('button', { name: 'Close Structured Retrieval execution details' })
    .click()

  // Replay: step back to the start, where no tool has run yet
  await workspace.getByRole('slider', { name: 'Replay position' }).press('Home')
  await expect(workspace.getByText('Step 0 of 13')).toBeVisible()
  await expect(workspace.locator('[data-node-id="tables-tool"]')).toHaveAttribute(
    'data-state',
    'pending'
  )

  expect(apiCalls).toEqual([])
})

test('the Agent Activity panel shows a recorded run: thinking and timeline', async ({ page }) => {
  await openSession(page, 'Returns policy and loyalty revenue')
  await page.getByRole('button', { name: 'Open agent activity panel' }).click()

  const thinking = page.getByRole('list', { name: 'Hermes thinking activity' })
  await expect(thinking.getByText('Request accepted')).toBeVisible()
  await expect(thinking.getByText('Table Query', { exact: true })).toBeVisible()
  await expect(thinking.getByText('Answer ready')).toBeVisible()

  await page.getByRole('tab', { name: 'Timeline' }).click()
  const timeline = page.getByRole('region', { name: 'Execution action timeline' })
  await expect(timeline.getByLabel('Timeline summary')).toContainText('106,217')
  await expect(page.getByRole('tab', { name: 'Benchmark' })).toHaveCount(0)
})

test('the data viewer replays the bundle’s copy of the database', async ({ page }) => {
  await openSession(page, 'Gold-tier orders and churn risk')
  await page.getByRole('button', { name: 'View execution for this response' }).first().click()
  const workspace = page.getByRole('region', { name: 'Execution workspace' })
  await workspace.getByRole('button', { name: 'Inspect DuckDB Tables' }).click()

  const browser = workspace.getByRole('dialog', { name: 'Structured Database browser' })
  await browser.getByRole('button', { name: /customers/ }).click()
  await expect(browser.getByText('Ada Park')).toBeVisible()
  await browser.getByRole('button', { name: 'SQL Query' }).click()
  await browser.getByRole('button', { name: 'Run query' }).click()
  await expect(browser.getByRole('region', { name: 'SQL results' })).toContainText('Cy Moreau')
})

test('a cited source opens its evidence in the execution view', async ({ page }) => {
  await openSession(page, 'Gold-tier orders and churn risk')
  const sources = page.getByRole('region', { name: 'Sources' }).first()
  await sources.locator('summary').click()
  await sources.getByRole('button', { name: 'Open this run in the execution view' }).click()

  const explorer = page.getByRole('dialog', { name: 'Auto Ontology text-to-SQL details' })
  await expect(explorer.getByRole('region', { name: 'Resolved phrases' })).toBeVisible()
  await expect(explorer.getByText(/SELECT count\(\*\) AS order_count/)).toBeVisible()
})

test('another pack replays its own recordings', async ({ page }) => {
  await openSession(page, 'Press line lockout', 'manufacturing')
  await expect(page.getByText('Isolate the press at the main disconnect')).toBeVisible()
  await page.getByRole('button', { name: 'View execution for this response' }).click()
  const workspace = page.getByRole('region', { name: 'Execution workspace' })
  // Without a rerank model, the evidence keeps Milvus's order
  await expect(workspace.locator('[data-node-id="nemotron-rerank"]')).toHaveAttribute(
    'data-state',
    'unobserved'
  )
  await expect(workspace.locator('[data-edge-id="milvus-evidence"]')).toHaveAttribute(
    'data-state',
    'completed'
  )
})
