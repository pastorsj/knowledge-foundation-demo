// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { expect, test, type Page } from '@playwright/test'
import { LIVE_URL, REPLAY_URL } from '../playwright.config'

/** The chat store's saved state: a live session whose job was running when the page closed. */
const SAVED_LIVE_SESSION = JSON.stringify({
  state: {
    currentUserId: 'local',
    currentConversation: null,
    conversations: [
      {
        id: 's_saved',
        userId: 'local',
        title: 'Which assets led?',
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

/**
 * Opens the live landing page with its featured questions. The server renders them only when the
 * API answers `GET /v1/pack` within 3 s, which a busy test run can miss now and then: reload until
 * they are there.
 */
const gotoLanding = async (page: Page) => {
  await expect(async () => {
    await page.goto('/')
    await expect(
      page.getByRole('region', { name: 'Featured questions' }).getByRole('link')
    ).toHaveCount(6, { timeout: 2_000 })
  }).toPass({ timeout: 20_000 })
}

test.describe('live mode', () => {
  test.use({ baseURL: LIVE_URL })

  test('a featured question is asked and answered with its cited evidence', async ({ page }) => {
    await gotoLanding(page)
    await expect(page).toHaveTitle('Enterprise Research')

    await page.getByRole('link', { name: /Market Leaders/ }).click()
    const composer = page.getByRole('textbox', { name: 'Chat message input' })
    await expect(composer).toHaveValue('Which assets led the market?')

    await page.getByRole('button', { name: 'Send message' }).click()

    await expect(page.getByText('Asset A led the market')).toBeVisible()
    const evidence = page.getByRole('region', { name: 'Sources' }).locator('summary')
    await expect(evidence).toContainText('Market analytics result — market scan')
    await evidence.click()
    await expect(evidence).toContainText('Close')
    await expect(composer).toBeEnabled()
  })

  test('the composer offers the pack examples as demo scenarios', async ({ page }) => {
    await page.goto('/research')
    const composer = page.getByRole('textbox', { name: 'Chat message input' })

    await page.getByTestId('demo-scenario-select').click()
    // The pack's examples in their order, only those whose data sources the API offers
    const options = page.getByRole('option')
    await expect(options).toHaveCount(7)
    expect(await options.evaluateAll((rows) => rows.map((row) => row.dataset.scenarioId))).toEqual([
      'unusual-sessions',
      'outcome-prediction',
      'peer-network',
      'sector-sql',
      'market-leaders',
      'news-and-filings',
      'news-sentiment-reaction',
    ])
    // Each with pills for the tools it is expected to use
    const peers = page.getByRole('option', { name: /Peer Network/ })
    await expect(peers.locator('.tool-pill')).toHaveText(['cuDF', 'cuGraph'])
    await expect(
      page.getByRole('option', { name: /Unusual Sessions/ }).locator('.tool-pill')
    ).toHaveText(['cuDF', 'cuML'])
    await peers.click()

    await expect(composer).toHaveValue(/^In the return-correlation network/)
    await expect(page.getByTestId('demo-scenario-select')).toContainText('Peer Network')
  })

  test('the example picker shows five rows and scrolls the others into view', async ({ page }) => {
    await page.goto('/research')
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
      whole: [
        'unusual-sessions',
        'outcome-prediction',
        'peer-network',
        'sector-sql',
        'market-leaders',
      ],
      cut: 0,
    })
    // The keyboard scrolls the list to the row it moves to
    for (let n = 0; n < 7; n++) await page.keyboard.press('ArrowDown')
    await expect(options.last()).toHaveAttribute('data-active-item')
    await expect.poll(shown).toEqual({
      whole: [
        'peer-network',
        'sector-sql',
        'market-leaders',
        'news-and-filings',
        'news-sentiment-reaction',
      ],
      cut: 0,
    })
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('demo-scenario-select')).toContainText('News & Price Reaction')
  })

  test('recorded sessions are listed beside My sessions and replay without the API', async ({
    page,
  }) => {
    const exports: string[] = []
    page.on('request', (request) => {
      if (new URL(request.url()).pathname.startsWith('/api/v1/jobs/')) exports.push(request.url())
    })
    await page.goto('/research')
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
    await page.goto('/research')
    const composer = page.getByRole('textbox', { name: 'Chat message input' })

    await page.getByRole('button', { name: 'Start voice input' }).click()
    await expect(page.getByRole('button', { name: 'Stop voice recording' })).toBeVisible()
    await page.waitForTimeout(800)
    await page.getByRole('button', { name: 'Stop voice recording' }).click()

    // The fake API transcribes any valid WAV recording to the same question
    await expect(composer).toHaveValue('Which assets led the market?')
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
        await gotoLanding(page)
        const featured = page.getByRole('region', { name: 'Featured questions' })
        await expect(featured.getByRole('link')).toHaveCount(6)

        const logos = page.locator('main [data-brand] img')
        await expect(logos).toHaveCount(14)
        await expect
          .poll(() =>
            logos.evaluateAll((images: HTMLImageElement[]) => images.map((i) => i.complete))
          )
          .not.toContain(false)
        const broken = await logos.evaluateAll((images: HTMLImageElement[]) =>
          images.filter((i) => i.naturalWidth === 0).map((i) => i.src)
        )
        expect(broken, `${where}: logos that did not load`).toEqual([])
        const langchain = page.locator('main [data-brand="LangChain"] img')
        await expect(langchain).toHaveAttribute('src', '/ecosystem-logos/langchain.svg')
        expect(await langchain.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0)

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
    await page.getByRole('link', { name: /Enter market analysis/ }).click()

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

  test('serves the health check and the data pack recordings', async ({ request }) => {
    expect(await (await request.get('/api/health')).json()).toEqual({
      status: 'ok',
      mode: 'replay',
    })
    expect(await (await request.get('/api/recordings/index.json')).json()).toMatchObject({
      schemaVersion: 2,
      pack: { id: 'e2e' },
    })
  })
})
