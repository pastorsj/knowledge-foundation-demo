// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import path from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { FAKE_API, LIVE_URL, REPLAY_URL } from '../playwright.config'

/** The chat store's saved state: a live session whose job was running when the page closed. */
const SAVED_LIVE_SESSION = JSON.stringify({
  state: {
    currentUserId: 'local',
    currentConversation: null,
    conversations: [
      {
        id: 's_saved',
        userId: 'local',
        title: 'Which customers spent the most?',
        packId: 'retail',
        createdAt: '2026-09-01T00:00:00.000Z',
        updatedAt: '2026-09-01T00:00:00.000Z',
        messages: [
          {
            id: 'answer',
            role: 'assistant',
            content: '',
            timestamp: '2026-09-01T00:00:00.000Z',
            messageType: 'agent_response',
            deepResearchJobId: 'job-1',
            deepResearchJobStatus: 'running',
            isDeepResearchActive: true,
          },
        ],
      },
    ],
  },
  version: 0,
})

const FILES = path.join(__dirname, 'fixtures', 'files')

/**
 * Opens the live landing page with its featured questions. The server renders them only when the
 * API answers `GET /v1/pack` within 3 s, which a busy test run can miss now and then: reload until
 * they are there.
 */
const gotoLanding = async (page: Page, query = '', count = 6) => {
  await expect(async () => {
    await page.goto(`/${query}`)
    await expect(
      page.getByRole('region', { name: 'Featured questions' }).getByRole('link')
    ).toHaveCount(count, { timeout: 2_000 })
  }).toPass({ timeout: 20_000 })
}

/** Picks an industry in the selector (the landing header's or the app bar's). */
const industrySelect = (page: Page) => page.getByTestId('industry-select').filter({ visible: true })

const pickIndustry = async (page: Page, name: string) => {
  await industrySelect(page).click()
  await page.getByRole('option', { name }).click()
}

const examples = (page: Page) =>
  page.getByRole('option').evaluateAll((rows) => rows.map((row) => row.dataset.scenarioId))

test.describe('live mode', () => {
  test.use({ baseURL: LIVE_URL })
  test.beforeEach(async ({ context }) => context.clearCookies())

  test('the landing page lists the industries, and switching one changes its questions and ?pack=', async ({
    page,
  }) => {
    await gotoLanding(page)
    await expect(page).toHaveTitle('NVIDIA Knowledge Foundation')
    await expect(
      page.getByRole('heading', { name: 'Ask your enterprise knowledge.' })
    ).toBeVisible()

    await industrySelect(page).click()
    await expect(page.getByRole('option')).toHaveText(['Manufacturing', 'Retail', 'Your data'])
    await page.getByRole('option', { name: 'Manufacturing' }).click()

    await expect(page).toHaveURL(/\?pack=manufacturing$/)
    const featured = page.getByRole('region', { name: 'Featured questions' })
    await expect(featured.getByRole('link')).toHaveCount(2)
    await expect(featured.getByRole('link', { name: /Press Lockout/ })).toHaveAttribute(
      'href',
      '/research?pack=manufacturing&question=press-lockout'
    )
    // The cookie remembers it: the landing page opens on Manufacturing again
    await gotoLanding(page, '', 2)
    await expect(industrySelect(page)).toContainText('Manufacturing')
  })

  test('a featured question is asked and answered with its cited evidence', async ({ page }) => {
    await gotoLanding(page)
    await page.getByRole('link', { name: /Top Customers/ }).click()
    const composer = page.getByRole('textbox', { name: 'Chat message input' })
    await expect(composer).toHaveValue('Which customers spent the most last quarter?')

    await page.getByRole('button', { name: 'Send message' }).click()

    await expect(page.getByText('Customer C2 spent the most last quarter')).toBeVisible()
    const evidence = page.getByRole('region', { name: 'Sources' }).locator('summary')
    await expect(evidence).toContainText('Structured query result — retail sales')
    await evidence.click()
    await expect(evidence).toContainText('Close')
    await expect(composer).toBeEnabled()
  })

  test('switching to Manufacturing changes the picker’s examples, the sources and ?pack=', async ({
    page,
  }) => {
    await page.goto('/research?pack=retail')
    await page.getByTestId('demo-scenario-select').click()
    // The pack's examples in their order, only those whose data sources the API offers
    expect(await examples(page)).toEqual([
      'churn-risk',
      'basket-size',
      'returns-and-revenue',
      'gold-orders',
      'top-customers',
      'returns-policy',
      'tier-revenue',
    ])
    await expect(page.getByRole('option', { name: /Churn Risk/ }).locator('.tool-pill')).toHaveText(
      ['DuckDB', 'Kumo']
    )
    await page.keyboard.press('Escape')
    const panel = page.getByRole('button', { name: 'Close data sources panel' }).locator('../..')
    await expect(page.getByText('Sales', { exact: true })).toBeVisible()

    await pickIndustry(page, 'Manufacturing')

    await expect(page).toHaveURL(/[?&]pack=manufacturing/)
    await expect(page.getByText('Procedures', { exact: true })).toBeVisible()
    await expect(page.getByText('Maintenance', { exact: true })).toBeVisible()
    await expect(page.getByText('Sales', { exact: true })).toHaveCount(0)
    await expect(panel).toBeVisible()
    await page.getByTestId('demo-scenario-select').click()
    await expect.poll(() => examples(page)).toEqual(['press-lockout', 'downtime', 'failure-risk'])
  })

  test('Your data: upload a PDF and a CSV, watch them ingest, and ask a cited question', async ({
    page,
  }) => {
    await page.goto('/research?pack=retail')
    await pickIndustry(page, 'Your data')
    await expect(page).toHaveURL(/[?&]pack=workspace/)

    // The panel opens to Files; the composer takes the files
    await expect(page.getByRole('radio', { name: 'Files' })).toBeChecked()
    await page
      .getByTestId('composer-file-input')
      .setInputFiles([path.join(FILES, 'policy.pdf'), path.join(FILES, 'orders.csv')])

    const cards = page.getByTestId('file-source-card')
    await expect(cards).toHaveCount(2)
    // Each file moves through its stages on each status poll, to Available
    await expect(cards.filter({ hasText: 'Available' })).toHaveCount(2, { timeout: 30_000 })
    await expect(page.getByRole('list', { name: 'Document pipeline' })).toHaveCount(1)
    await expect(page.getByRole('list', { name: 'Table pipeline' })).toHaveCount(1)
    await expect(cards.filter({ hasText: 'policy.pdf' }).getByTestId('file-parser')).toHaveText(
      'Nemotron Parse 2.0'
    )
    await expect(cards.filter({ hasText: 'orders.csv' })).toContainText('orders')

    // The sources panel lists Your documents and Your tables
    await page.getByRole('radio', { name: 'Connections' }).click()
    await expect(page.getByText('Your documents', { exact: true })).toBeVisible()
    await expect(page.getByText('Your tables', { exact: true })).toBeVisible()

    const composer = page.getByRole('textbox', { name: 'Chat message input' })
    await composer.fill('What is the return window, and which order was the largest?')
    await page.getByRole('button', { name: 'Send message' }).click()
    await expect(page.getByText('Opened items may be returned within 30 days')).toBeVisible()
    await expect(page.getByRole('region', { name: 'Sources' }).locator('summary')).toHaveCount(2)
  })

  test('Your data: a file the pipeline cannot read fails at its step, with the reason', async ({
    page,
    request,
  }) => {
    await page.goto('/research?pack=workspace')
    await expect(page.getByRole('radio', { name: 'Files' })).toBeChecked()
    await page.getByTestId('composer-file-input').setInputFiles({
      name: 'unreadable-scan.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 not a PDF'),
    })
    const card = page.getByTestId('file-source-card').filter({ hasText: 'unreadable-scan.pdf' })
    await expect(card).toHaveAttribute('data-status', 'error', { timeout: 30_000 })
    // The step it failed in, said in words too, and why
    await expect(card.locator('li[data-state="failed"]')).toHaveText('Parse: failed')
    await expect(card).toContainText('Nemotron Parse could not read this file')
    await expect(card.getByTestId('file-stage-announcement')).toHaveText(
      'unreadable-scan.pdf: failed at Parse'
    )

    // Leave Your data as it was for the other tests
    const listed = (await (
      await request.get(`${FAKE_API}/v1/collections/workspace/documents`)
    ).json()) as {
      files: Array<{ file_id: string; file_name: string }>
    }
    const ids = listed.files
      .filter((f) => f.file_name === 'unreadable-scan.pdf')
      .map((f) => f.file_id)
    await request.delete(`${FAKE_API}/v1/collections/workspace/documents`, {
      data: { file_ids: ids },
    })
  })

  test('Your data: with the ingest service down, the Files tab says so, keeps the upload zone, and retries', async ({
    page,
    request,
  }) => {
    await request.post(`${FAKE_API}/__fake/ingest-down?times=1`)
    await page.goto('/research?pack=workspace')
    const banner = page.getByTestId('files-error')
    await expect(banner).toContainText('Couldn’t reach the ingestion service')
    await expect(banner).toContainText('The ingest service is unavailable.')
    await expect(page.getByText('Checking for files...')).toHaveCount(0)
    await expect(page.getByTestId('accepted-types')).toBeVisible()
    await banner.getByRole('button', { name: 'Retry' }).click()
    await expect(banner).toHaveCount(0)
  })

  test('while the API has no knowledge catalog (a 503), the landing and the sources say so', async ({
    page,
  }) => {
    await page.goto('/?pack=uncataloged')
    await expect(page.getByTestId('catalog-notice')).toContainText(
      'The knowledge catalog is not ready yet'
    )
    await expect(page.getByRole('region', { name: 'Featured questions' })).toHaveCount(0)

    await page.goto('/research?pack=uncataloged')
    await expect(page.getByText('Data sources not ready yet')).toBeVisible()
    await expect(page.getByText(/The knowledge catalog is being built/)).toBeVisible()
  })

  test('a pack still syncing says so on the landing page and on its sources', async ({ page }) => {
    await page.goto('/?pack=syncing')
    await expect(page.getByTestId('catalog-notice')).toContainText('Syncing Retail')
    await expect(page.getByRole('region', { name: 'Featured questions' })).toHaveCount(0)

    await page.goto('/research?pack=syncing')
    await expect(page.getByTestId('source-status').first()).toHaveText('Ingesting')
  })

  test('the example picker shows five rows and scrolls the others into view', async ({ page }) => {
    await page.goto('/research?pack=retail')
    await page.getByTestId('demo-scenario-select').click()
    const list = page.getByTestId('demo-scenario-list')
    const options = page.getByRole('option')
    await expect(options).toHaveCount(7)

    /** The options shown whole in the list's scrollport, and whether any other one shows in part */
    const shown = () =>
      list.evaluate((element) => {
        const port = element.getBoundingClientRect()
        const top = port.top + element.clientTop
        const bottom = top + element.clientHeight
        const rows = [...element.querySelectorAll<HTMLElement>('[role="option"]')]
        const whole = rows.filter((row) => {
          const box = row.getBoundingClientRect()
          return box.top >= top - 0.5 && box.bottom <= bottom + 0.5
        })
        const cut = rows.filter((row) => {
          const box = row.getBoundingClientRect()
          return !whole.includes(row) && box.bottom > top + 0.5 && box.top < bottom - 0.5
        })
        return { whole: whole.map((row) => row.dataset.scenarioId), cut: cut.length }
      })

    // Exactly five rows, none cut; the others are underneath
    await expect.poll(shown).toEqual({
      whole: ['churn-risk', 'basket-size', 'returns-and-revenue', 'gold-orders', 'top-customers'],
      cut: 0,
    })
    // The keyboard scrolls the list to the row it moves to
    for (let n = 0; n < 7; n++) await page.keyboard.press('ArrowDown')
    await expect(options.last()).toHaveAttribute('data-active-item')
    await expect.poll(shown).toEqual({
      whole: [
        'returns-and-revenue',
        'gold-orders',
        'top-customers',
        'returns-policy',
        'tier-revenue',
      ],
      cut: 0,
    })
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('demo-scenario-select')).toContainText('Revenue by Tier')
  })

  test('recorded sessions are listed beside My sessions and replay without the API', async ({
    page,
  }) => {
    const exports: string[] = []
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/jobs/')) exports.push(request.url())
    })
    await page.goto('/research?pack=retail')
    await expect(page.getByRole('tab', { name: 'My sessions' })).toHaveAttribute(
      'aria-selected',
      'true'
    )

    await page.getByRole('tab', { name: /^Recorded \(\d+\)$/ }).click()
    await page
      .getByRole('button', { name: /^Recorded session: / })
      .first()
      .click()

    const composer = page.getByRole('textbox', { name: 'Chat message input' })
    await expect(composer).toBeDisabled()
    await expect(page.getByText('Recorded test session · read only')).toBeVisible()
    await page.getByRole('button', { name: 'View execution for this response' }).first().click()
    const workspace = page.getByRole('region', { name: 'Execution workspace' })
    await expect(workspace.getByText('Hermes Recorded')).toBeVisible()
    await expect(workspace.getByText(/^Step (\d+) of \1$/)).toBeVisible()
    expect(exports).toEqual([])
  })

  test('voice input records a question and puts its transcript in the composer', async ({
    page,
  }) => {
    await page.goto('/research?pack=retail')
    const composer = page.getByRole('textbox', { name: 'Chat message input' })

    await page.getByRole('button', { name: 'Start voice input' }).click()
    await expect(page.getByRole('button', { name: 'Stop voice recording' })).toBeVisible()
    await page.waitForTimeout(800)
    await page.getByRole('button', { name: 'Stop voice recording' }).click()

    // The fake API transcribes any valid WAV recording to the same question
    await expect(composer).toHaveValue('Which customers spent the most last quarter?')
    await expect(page.getByRole('button', { name: 'Start voice input' })).toBeEnabled()
  })

  test('the landing page fits a 1280x800, 1440x900 or 1920x1080 screen without scrolling, and its logos load', async ({
    page,
  }) => {
    for (const viewport of [
      { width: 1280, height: 800 },
      { width: 1440, height: 900 },
      { width: 1920, height: 1080 },
    ]) {
      await page.setViewportSize(viewport)
      for (const colorScheme of ['light', 'dark'] as const) {
        const where = `${viewport.width}x${viewport.height} ${colorScheme}`
        await page.emulateMedia({ colorScheme })
        await gotoLanding(page, '?pack=retail')

        const logos = page.locator('main [data-brand] img')
        await expect(logos).toHaveCount(12)
        await expect
          .poll(() =>
            logos.evaluateAll((images: HTMLImageElement[]) => images.map((i) => i.complete))
          )
          .not.toContain(false)
        const broken = await logos.evaluateAll((images: HTMLImageElement[]) =>
          images.filter((i) => i.naturalWidth === 0).map((i) => i.src)
        )
        expect(broken, `${where}: logos that did not load`).toEqual([])

        const size = await page.evaluate(() => ({
          scrollHeight: document.documentElement.scrollHeight,
          innerHeight: window.innerHeight,
        }))
        expect(size.scrollHeight, `${where}: page height`).toBeLessThanOrEqual(size.innerHeight)
      }
    }
  })
})

test.describe('replay mode', () => {
  test.use({ baseURL: REPLAY_URL })

  test('the research view never calls the API and its composer is read only', async ({ page }) => {
    const apiCalls: string[] = []
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/')) apiCalls.push(request.url())
    })
    await page.addInitScript((saved) => {
      if (!localStorage.getItem('kf-chat-store')) localStorage.setItem('kf-chat-store', saved)
    }, SAVED_LIVE_SESSION)

    await page.goto('/')
    await page.getByRole('link', { name: /Ask your knowledge/ }).click()

    await expect(page.getByText('What do you want to know?')).toBeVisible()
    await expect(page.getByRole('textbox', { name: 'Chat message input' })).toBeDisabled()
    await expect(page.getByText('Recorded test session · read only')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add data sources' })).toBeDisabled()
    // The saved live session stays out of the replay's lists
    await page.getByRole('tab', { name: 'My sessions' }).click()
    await expect(page.getByText('Replay mode shows the recorded sessions only.')).toBeVisible()
    expect(apiCalls).toEqual([])
    const saved = await page.evaluate(() => localStorage.getItem('kf-chat-store'))
    expect(JSON.parse(saved!).state.conversations[0].messages[0].deepResearchJobStatus).toBe(
      'running'
    )
  })

  test('the industry selector lists the recorded packs, and each pack lists its own recordings', async ({
    page,
  }) => {
    await page.goto('/research?pack=retail')
    await expect(page.getByRole('button', { name: /^Recorded session: / })).toHaveCount(2)

    await pickIndustry(page, 'Manufacturing')
    await expect(page).toHaveURL(/[?&]pack=manufacturing/)
    await expect(page.getByRole('button', { name: /^Recorded session: / })).toHaveCount(1)
    await expect(
      page.getByRole('button', { name: 'Recorded session: Press line lockout; Completed' })
    ).toBeVisible()
  })

  test('serves the health check, the packs and their recordings', async ({ request }) => {
    expect(await (await request.get('/api/health')).json()).toEqual({
      status: 'ok',
      mode: 'replay',
    })
    expect(await (await request.get('/api/recordings/packs.json')).json()).toMatchObject({
      packs: [{ id: 'manufacturing' }, { id: 'retail' }],
    })
    expect(await (await request.get('/api/recordings/retail/index.json')).json()).toMatchObject({
      schemaVersion: 2,
      pack: { id: 'retail' },
    })
    expect((await request.get('/api/recordings/../secret.json')).status()).toBe(404)
  })
})
