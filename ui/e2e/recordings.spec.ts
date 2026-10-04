// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Every recorded session of every pack whose recordings bundle is in the checkout, as
 * `scripts/demo.sh replay` serves it (a pack without one, until `scripts/demo.sh record` writes it,
 * is skipped). Per session: its Recorded list entry with the tool pills its runs used;
 * then per turn the question, the answer, its cited sources, and its run in the execution view down
 * to the closing events (the answer, the citation resolution and the run metrics). Nothing calls
 * the API.
 *
 * The expectations come from the bundles themselves, so a re-recording needs no change here; the
 * tool pills are derived again from the recorded events and the tool registry.
 */

import { readFileSync } from 'node:fs'
import { expect, test, type Locator, type Page } from '@playwright/test'
import { DATA_PACKS_DIR, RECORDED_PACKS, UNRECORDED_PACKS } from '../playwright.config'

interface RecordedEvent {
  eventId: string
  eventKind: string
  invocationId?: string | null
  toolName?: string | null
  artifactRefs?: string[]
  display: { label: string; attributes: Record<string, unknown> }
}

interface Citation {
  number: number
  label: string
}

interface RecordedTurn {
  jobId: string
  question: string
  status: string
  report: { markdown: string; citations: Citation[] } | null
  events: RecordedEvent[]
  receipts: { receiptId: string }[]
}

interface ToolPillUse {
  pill: string
}

interface IndexSession {
  id: string
  title: string
  turns: { jobId: string; question: string }[]
  tools?: ToolPillUse[]
}

/** The UI's pill labels and families: NVIDIA's tools, and partners' (DuckDB). */
const PILLS: Record<string, { label: string; family: 'partner' | 'nvidia' }> = {
  retrieval: { label: 'Retrieval', family: 'nvidia' },
  duckdb: { label: 'DuckDB', family: 'partner' },
  kumo: { label: 'Kumo', family: 'nvidia' },
  ontology: { label: 'Ontology', family: 'nvidia' },
}
const PILL_ORDER = Object.keys(PILLS)

const REGISTRY = new Map(
  (
    JSON.parse(readFileSync(`${process.cwd()}/../contracts/tool-registry.json`, 'utf8')) as {
      tools: { id: string; pills: string[] }[]
    }
  ).tools.map((tool) => [tool.id, tool])
)

const orderPills = (pills: ToolPillUse[]): ToolPillUse[] =>
  [...pills].sort((a, b) => PILL_ORDER.indexOf(a.pill) - PILL_ORDER.indexOf(b.pill) || 0)

/**
 * The pills of a session's runs (api/src/demo_api/pills.py): each completed registered tool call
 * brings its registry pills.
 */
const pillsOfRuns = (turns: RecordedTurn[]): ToolPillUse[] => {
  const found = new Map<string, ToolPillUse>()
  for (const turn of turns) {
    for (const event of turn.events) {
      const tool = event.eventKind === 'artifact.available' && REGISTRY.get(event.toolName ?? '')
      if (!tool) continue
      for (const pill of tool.pills) found.set(pill, { pill })
    }
  }
  return orderPills([...found.values()])
}

const pillLabel = ({ pill }: ToolPillUse) => PILLS[pill].label

/** The answer's trailing references block, which the UI shows as its Sources list */
const REFERENCES_BLOCK =
  /\n{1,2}(?:\*\*References:?\*\*|#{2,3}\s+(?:References|Sources))\s*\n[\s\S]*$/i

/**
 * The words and numbers of a text, in order. Markdown syntax, link targets, HTML tags and entities
 * are not shown, so they are dropped.
 */
const words = (text: string): string[] =>
  text
    .replace(/\]\([^)\s]*\)/g, ']')
    .replace(/<[^>\n]+>/g, ' ')
    .replace(/&[a-z]+;|&#\d+;/gi, ' ')
    .toLowerCase()
    .match(/\p{L}{2,}|\p{N}+/gu) ?? []

/** An inline citation marker, which the UI shows as a superscript chip (rehype-citations.ts) */
const CITATION_MARKER = /(?<!\w)\[\d{1,3}\]/g

/**
 * The words of a recorded answer that the chat shows as text: without its references block (the
 * Sources list), its citation markers (chips) and the numbers of its ordered lists (list markers).
 */
const answerWords = (markdown: string): string[] =>
  words(
    markdown
      .replace(REFERENCES_BLOCK, '')
      .replace(CITATION_MARKER, ' ')
      .replace(/^[ \t]*\d+[.)][ \t]/gm, ' ')
  )

/** The text an answer shows, without its citation chips (a chip's number would join a figure). */
const textWithoutCitations = (answer: Locator): Promise<string> =>
  answer.evaluate((element: HTMLElement) => {
    const chips = Array.from(element.querySelectorAll<HTMLElement>('sup'))
    for (const chip of chips) chip.style.display = 'none'
    const text = element.innerText
    for (const chip of chips) chip.style.removeProperty('display')
    return text
  })

/** The step label the replay bar shows on a run's last event (ExecutionWorkspace's stepLabel) */
const CLOSING_LABELS: Record<string, string> = {
  'run.completed': 'Answer complete',
  'run.failed': 'Run failed',
  'report.completed': 'Response formatted',
  'report.reference_resolution': 'Citations resolved',
  'report.metrics': 'Run metrics available',
}

const lastOf = (events: RecordedEvent[], kind: string) =>
  [...events].reverse().find((event) => event.eventKind === kind)

const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/**
 * Asserts that `actual` holds every word of `expected`, in order: the words of `expected` left out of
 * the longest common subsequence of the two must be none.
 */
const expectWordsInOrder = (actual: string[], expected: string[], what: string) => {
  const [n, m] = [expected.length, actual.length]
  const longest = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      longest[i][j] =
        expected[i] === actual[j]
          ? longest[i + 1][j + 1] + 1
          : Math.max(longest[i + 1][j], longest[i][j + 1])
    }
  }
  const missing: string[] = []
  for (let i = 0, j = 0; i < n;) {
    if (j < m && expected[i] === actual[j]) [i, j] = [i + 1, j + 1]
    else if (j < m && longest[i][j + 1] >= longest[i + 1][j]) j++
    else missing.push(`${expected[i++]} (word ${i})`)
  }
  expect(missing, `${what}: words of the recorded answer not shown, in order`).toEqual([])
}

const recordedEntry = async (page: Page, pack: string, title: string): Promise<Locator> => {
  await page.goto(`/research?pack=${pack}`)
  const entry = page.getByRole('button', {
    name: new RegExp(`^Recorded session: ${escapeRegExp(title)}; `),
  })
  await expect(entry).toHaveCount(1)
  return entry
}

for (const pack of UNRECORDED_PACKS) {
  test(`${pack} recordings`, () => {
    test.skip(true, `${pack} has no recordings bundle in this checkout`)
  })
}

for (const [pack, baseURL] of Object.entries(RECORDED_PACKS)) {
  const readJson = <T>(file: string): T =>
    JSON.parse(readFileSync(`${DATA_PACKS_DIR}/${pack}/recordings/${file}`, 'utf8'))
  const index = readJson<{ pack: { id: string }; sessions: IndexSession[] }>('index.json')

  test.describe(`${pack} recordings`, () => {
    test.use({ baseURL })

    test('the index lists its pack and sessions with distinct ids and titles', () => {
      expect(index.pack.id).toBe(pack)
      expect(index.sessions.length).toBeGreaterThan(0)
      const ids = index.sessions.map((session) => session.id)
      const titles = index.sessions.map((session) => session.title)
      expect(new Set(ids).size, 'distinct session ids').toBe(ids.length)
      expect(new Set(titles).size, 'distinct session titles').toBe(titles.length)
    })

    for (const session of index.sessions) {
      test(`"${session.title}" replays every turn: answer, citations, pills and closing events`, async ({
        page,
      }) => {
        const { turns, title } = readJson<{ title: string; turns: RecordedTurn[] }>(
          `sessions/${session.id}.json`
        )
        const apiCalls: string[] = []
        page.on('request', (request) => {
          if (new URL(request.url()).pathname.startsWith('/api/v1/')) apiCalls.push(request.url())
        })

        await test.step('the session file matches its index entry', () => {
          expect(title).toBe(session.title)
          expect(turns.map(({ jobId, question }) => ({ jobId, question }))).toEqual(
            session.turns.map(({ jobId, question }) => ({ jobId, question }))
          )
        })

        const pills = pillsOfRuns(turns)
        await test.step('its Recorded list entry shows the tools its runs used', async () => {
          if (session.tools) {
            expect(
              orderPills(session.tools.map(({ pill }) => ({ pill }))),
              'the index pills are the ones its runs used'
            ).toEqual(pills)
          }
          const entry = await recordedEntry(page, pack, title)
          await expect(entry).toContainText(`${turns.length} turn${turns.length === 1 ? '' : 's'}`)
          await expect(entry.locator('.tool-pill')).toHaveText(pills.map(pillLabel))
          for (const [i, use] of pills.entries()) {
            await expect(entry.locator('.tool-pill').nth(i)).toHaveAttribute(
              'data-family',
              PILLS[use.pill].family
            )
          }
          await entry.click()
        })

        const answers = page.locator('.agent-final-response')
        const viewRuns = page.getByRole('button', { name: 'View execution for this response' })
        await expect(answers).toHaveCount(turns.length)
        await expect(viewRuns).toHaveCount(turns.length)

        for (const [i, turn] of turns.entries()) {
          const where = `turn ${i + 1} of ${turns.length}`
          await test.step(`${where}: the question and the answer`, async () => {
            expect(turn.status, `${where} status`).toBe('success')
            await expect(
              page.locator('.user-message-bubble').getByText(turn.question, { exact: true })
            ).toBeVisible()
            const recorded = answerWords(turn.report?.markdown ?? '')
            expect(recorded.length, `${where} has an answer`).toBeGreaterThan(0)
            const shown = await textWithoutCitations(answers.nth(i).locator('.answer-reveal'))
            expectWordsInOrder(words(shown), recorded, where)
          })

          await test.step(`${where}: the cited sources`, async () => {
            const citations = turn.report?.citations ?? []
            const resolution = lastOf(turn.events, 'report.reference_resolution')
            if (resolution) {
              expect(resolution.display.attributes.total_citations, `${where} citations`).toBe(
                citations.length
              )
            }
            const sources = answers.nth(i).getByRole('region', { name: 'Sources' })
            if (!citations.length) {
              await expect(sources).toHaveCount(0)
              return
            }
            const items = sources.getByRole('listitem')
            await expect(items).toHaveCount(citations.length)
            for (const [n, citation] of citations.entries()) {
              await expect(items.nth(n).locator('sup')).toHaveText(String(citation.number))
              await expect(items.nth(n).locator('summary')).toContainText(citation.label)
            }
          })

          await test.step(`${where}: its run, down to the closing events`, async () => {
            const last = turn.events[turn.events.length - 1]
            expect(Object.keys(CLOSING_LABELS), `${where} ends on a closing event`).toContain(
              last.eventKind
            )
            for (const kind of ['run.completed', 'report.completed']) {
              expect(lastOf(turn.events, kind), `${where} has ${kind}`).toBeTruthy()
            }

            await viewRuns.nth(i).click()
            const workspace = page.getByRole('region', { name: 'Execution workspace' })
            const steps = turn.events.length
            const replay = workspace.getByLabel('Execution replay controls')
            await expect(replay).toContainText(`Step ${steps} of ${steps}`)
            await expect(replay).toContainText(CLOSING_LABELS[last.eventKind])
            await expect(
              workspace.getByRole('list', { name: 'Execution graph legend' })
            ).toBeVisible()

            const summary = workspace.getByRole('region', { name: 'Hermes run summary' })
            const resolution = lastOf(turn.events, 'report.reference_resolution')
            if (resolution) {
              const { status, total_citations: cited } = resolution.display.attributes
              // Capitalized by CSS
              const publication =
                status === 'reference_ids_resolved'
                  ? 'Citation/Reference IDs resolved'
                  : String(status).replaceAll('_', ' ')
              await expect(summary).toContainText(new RegExp(escapeRegExp(publication), 'i'))
              await expect(summary).toContainText(`${cited} cited evidence item(s)`)
            }
            const metrics = lastOf(turn.events, 'report.metrics')?.display.attributes
            if (metrics) {
              await expect(summary).toContainText(`${metrics.tool_call_count} tool call(s) ·`)
              if (metrics.runtime_profile) {
                await expect(summary).toContainText(`Runtime profile: ${metrics.runtime_profile}`)
              }
            }

            await workspace.getByRole('button', { name: 'Back to Answer' }).click()
            await expect(answers).toHaveCount(turns.length)
          })
        }

        await test.step('the Agent Activity panel shows the answer and its citations', async () => {
          await page.getByRole('button', { name: 'Open agent activity panel' }).click()
          await expect(
            page.getByRole('list', { name: 'Hermes thinking activity' }).getByText('Answer ready')
          ).toBeVisible()
          if (lastOf(turns[turns.length - 1].events, 'report.reference_resolution')) {
            await page.getByRole('tab', { name: 'Timeline' }).click()
            await expect(
              page
                .getByRole('region', { name: 'Execution action timeline' })
                .getByText('Citations resolved', { exact: true })
            ).toBeVisible()
          }
        })

        expect(apiCalls).toEqual([])
      })
    }
  })
}
