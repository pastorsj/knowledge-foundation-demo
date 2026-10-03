// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The execution view in replay mode, on the synthetic v2 bundle in
 * e2e/fixtures/packs/e2e/recordings (built from contracts/fixtures).
 */

import { expect, test, type Page } from '@playwright/test'
import { REPLAY_URL } from '../playwright.config'

test.use({ baseURL: REPLAY_URL })

const openSession = async (page: Page, title: string) => {
  await page.goto('/research')
  await page
    .getByRole('button', { name: `Recorded session: ${title}; Completed` })
    .first()
    .click()
}

test('the replays list shows the tools each recorded run used', async ({ page }) => {
  await page.goto('/research')
  const pills = (title: string) =>
    page
      .getByRole('button', { name: `Recorded session: ${title}; Completed` })
      .locator('.tool-pill')

  // Listed in the bundle's index, as `demo-api record` writes them
  await expect(pills('Unusual moves and filings')).toHaveText(['cuDF', 'cuML', 'Retrieval'])
  // Derived from the recorded events, for an index that predates them
  await expect(pills('Dividends and news likelihood')).toHaveText(['Kumo', 'Ontology'])
  await expect(pills('Unusual moves and filings').first()).toHaveAttribute('data-family', 'rapids')
  await expect(pills('Dividends and news likelihood').first()).toHaveAttribute(
    'data-family',
    'nvidia'
  )
})

test('a recorded session replays its run as a graph and opens an explorer', async ({ page }) => {
  const apiCalls: string[] = []
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/')) apiCalls.push(request.url())
  })

  await openSession(page, 'Unusual moves and filings')
  await expect(page.getByText('Asset Delta showed the most unusual combination')).toBeVisible()
  await page.getByRole('button', { name: 'View execution for this response' }).click()

  const workspace = page.getByRole('region', { name: 'Execution workspace' })
  await expect(workspace.getByRole('heading', { name: 'Execution Graph' })).toBeVisible()
  await expect(workspace.getByText('Hermes Recorded')).toBeVisible()
  await expect(workspace.getByText('Step 10 of 10')).toBeVisible()
  const summary = workspace.getByRole('region', { name: 'Hermes run summary' })
  await expect(summary.getByText('2 tool call(s) ·', { exact: false })).toBeVisible()
  await expect(workspace.locator('[data-group-id]')).toHaveCount(5)
  await expect(workspace.getByRole('button', { name: 'Inspect Hermes Agent' })).toBeVisible()

  // Retrieve Evidence carries the LangChain logo, and it loads
  const retrieval = workspace.locator('[data-node-id="retriever-tool"]')
  await expect(retrieval).toHaveAttribute('data-state', 'completed')
  const langchain = retrieval.getByRole('img', { name: 'LangChain' })
  await expect(langchain).toHaveAttribute('src', '/ecosystem-logos/langchain.svg')
  await expect
    .poll(() => langchain.evaluate((image: HTMLImageElement) => image.naturalWidth))
    .toBeGreaterThan(0)

  // The anomaly scan ran on the GPU; its node says so and opens its explorer
  const anomaly = workspace.locator('[data-node-id="market-anomaly-scan"]')
  await expect(anomaly).toHaveAttribute('data-gpu-accelerated', 'true')
  await anomaly.click()
  const explorer = workspace.getByRole('dialog', { name: 'Market Anomaly Scan explorer' })
  await expect(explorer.getByText('NVIDIA GPU tool receipt')).toBeVisible()
  await expect(explorer.getByText('cuML 26.6.0')).toBeVisible()
  await expect(explorer.getByLabel('Anomaly score ranking')).toBeVisible()
  await explorer.getByRole('button', { name: 'Close Market Anomaly Scan explorer' }).click()

  // Replay: step back to the start, where no tool has run yet
  await workspace.getByRole('slider', { name: 'Replay position' }).press('Home')
  await expect(workspace.getByText('Step 0 of 10')).toBeVisible()
  await expect(anomaly).toHaveAttribute('data-state', 'pending')

  expect(apiCalls).toEqual([])
})

test('the Agent Activity panel shows a recorded run: thinking, timeline and its benchmark', async ({
  page,
}) => {
  await openSession(page, 'Unusual moves and filings')
  await page.getByRole('button', { name: 'Open agent activity panel' }).click()

  const thinking = page.getByRole('list', { name: 'Hermes thinking activity' })
  await expect(thinking.getByText('Request accepted')).toBeVisible()
  await expect(thinking.getByText('Market Anomaly Scan', { exact: true })).toBeVisible()
  await expect(thinking.getByText('Answer ready')).toBeVisible()

  await page.getByRole('tab', { name: 'Timeline' }).click()
  const timeline = page.getByRole('region', { name: 'Execution action timeline' })
  await expect(timeline.getByLabel('Timeline summary')).toContainText('106,217')
  await expect(
    timeline.getByRole('list', { name: 'Observed execution actions' }).getByRole('listitem')
  ).toHaveCount(3)

  await page.getByRole('tab', { name: 'Benchmark' }).click()
  await expect(page.getByText('Recorded benchmark')).toBeVisible()
  await expect(page.getByTestId('benchmark-tool-time-ratio')).toHaveText('1.86× faster')
  await expect(page.getByText('1.9× · Qualified speedup')).toBeVisible()

  // The recording also carries the Milvus comparison of a GPU stack: the CPU index against its GPU copy
  const milvus = page.getByTestId('retrieval-benchmark-panel')
  await expect(milvus).toContainText('Milvus Vector Search')
  await expect(milvus).toContainText('1.2× faster vector search') // concurrent requests first
  await expect(milvus.getByTestId('retrieval-benchmark-gpu')).toContainText(
    'GPU_IVF_FLAT · NVIDIA cuVS'
  )
  await expect(milvus.getByTestId('retrieval-benchmark-cpu')).toContainText('HNSW')
  await milvus.getByRole('button', { name: 'Single query' }).click()
  await expect(milvus).toContainText('CPU faster or equal for this workload')
})

test('the data viewer replays the bundle’s copy of the database', async ({ page }) => {
  await openSession(page, 'Dividends and news likelihood')
  await page.getByRole('button', { name: 'View execution for this response' }).first().click()
  const workspace = page.getByRole('region', { name: 'Execution workspace' })
  await workspace.getByRole('button', { name: 'Inspect Structured Database' }).click()

  const browser = workspace.getByRole('dialog', { name: 'Structured Database browser' })
  await browser.getByRole('button', { name: /corporate_actions/ }).click()
  await expect(browser.getByText('ca-001')).toBeVisible()
  await browser.getByRole('button', { name: 'SQL Query' }).click()
  await browser.getByRole('button', { name: 'Run query' }).click()
  await expect(browser.getByRole('region', { name: 'SQL results' })).toContainText('asset-meridian')
})

test('a cited source opens its evidence in the execution view', async ({ page }) => {
  await openSession(page, 'Dividends and news likelihood')
  const sources = page.getByRole('region', { name: 'Sources' }).first()
  await sources.locator('summary').click()
  await sources.getByRole('button', { name: 'Open this run in the execution view' }).click()

  const explorer = page.getByRole('dialog', { name: 'Auto Ontology text-to-SQL details' })
  await expect(explorer.getByRole('region', { name: 'Resolved phrases' })).toBeVisible()
  await expect(explorer.getByText('main.corporate_actions.cash_amount_usd')).toBeVisible()
  await expect(explorer.getByText(/WITH price_history AS/)).toBeVisible()
})
