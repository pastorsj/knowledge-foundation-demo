// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@/test-utils'
import { useLayoutStore } from '@/features/layout/store'
import { resetReplayDatabase } from './data-viewer/database-client'
import { ExecutionWorkspace } from './ExecutionWorkspace'
import { useExecutionStore } from './store'
import { fixtureEvents, publicationEvents, readRecording, receiptOf } from './test-utils/fixtures'
import type { ExecutionRecord } from './store'

const JOB = fixtureEvents[0].jobId
const turn = {
  jobId: JOB,
  question: 'What is the return window, and which loyalty tier brought the most revenue?',
  events: fixtureEvents,
  receipts: [receiptOf('structured_query'), receiptOf('retrieval_evidence')],
  report: {
    citations: [{ number: 1, evidenceId: receiptOf('structured_query').receiptId }],
  },
}

const renderWorkspace = (
  mode: 'live' | 'replay',
  focus: { referenceId?: string } | null = null
) => {
  const onClose = vi.fn()
  const view = render(
    <ExecutionWorkspace jobId={JOB} focus={focus} question={turn.question} onClose={onClose} />,
    { config: { mode } }
  )
  return { ...view, onClose }
}

/** Replay reads the bundle's copy of the database; these runs have none unless a test says so. */
const serveDatabase = (database: unknown = null) =>
  vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(async () =>
      database ? Response.json(database) : new Response(null, { status: 404 })
    )

describe('ExecutionWorkspace', () => {
  beforeEach(() => {
    useExecutionStore.setState({ runs: {}, dropped: 0 })
    useLayoutStore.setState({ packId: 'retail' })
    resetReplayDatabase()
  })
  afterEach(() => vi.restoreAllMocks())

  it('replays a recorded run: header, replay bar, run summary, graph and an explorer', () => {
    serveDatabase()
    useExecutionStore.getState().addRecord(turn)
    const { onClose } = renderWorkspace('replay')

    const workspace = screen.getByRole('region', { name: 'Execution workspace' })
    expect(within(workspace).getByRole('heading', { name: 'Execution Graph' })).toBeVisible()
    expect(within(workspace).getByText('Hermes Recorded')).toBeVisible()
    expect(within(workspace).getByText('Step 10 of 10')).toBeVisible()
    expect(within(workspace).getByText('Answer complete')).toBeVisible()
    const summary = screen.getByRole('region', { name: 'Hermes run summary' })
    expect(within(summary).getByText('Citation/reference IDs resolved')).toBeVisible()
    expect(
      within(summary).getByText('1 cited evidence item(s) · 1 available but uncited')
    ).toBeVisible()
    expect(within(summary).getByText('106,217 tokens')).toBeVisible()

    // The DuckDB tables node carries DuckDB's mark, the Milvus node its logo
    expect(document.querySelector('[data-node-id="structured-database"] [role="img"]')).toHaveAttribute(
      'aria-label',
      'DuckDB'
    )
    expect(
      within(document.querySelector('[data-node-id="milvus"]') as HTMLElement).getByRole('img', {
        name: 'Milvus',
      })
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Inspect Structured Retrieval' }))
    const explorer = screen.getByRole('dialog', { name: 'Structured Retrieval execution details' })
    expect(within(explorer).getByRole('heading', { name: 'DuckDB Table Query' })).toBeVisible()
    expect(within(explorer).getByText('Generated SQL')).toBeVisible()
    expect(within(explorer).getByTestId('execution-evidence-output')).toHaveTextContent('gold')
    // A bundle without a copy of the database has nothing to open in the data viewer
    expect(within(explorer).queryByRole('button', { name: 'Open in Data Viewer' })).toBeNull()
    // The explorer covers the graph, and the header gives way to it
    expect(screen.queryByRole('heading', { name: 'Execution Graph' })).toBeNull()

    fireEvent.click(
      within(explorer).getByRole('button', {
        name: 'Close Structured Retrieval execution details',
      })
    )
    fireEvent.click(screen.getByRole('button', { name: /Back to Answer/ }))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows the publication the API recorded: its last steps, citation resolution and run metrics', () => {
    serveDatabase()
    const events = [
      ...fixtureEvents,
      ...publicationEvents(
        {
          status: 'reference_ids_resolved',
          total_citations: 1,
          uncited_evidence_count: 0,
          invalid_evidence_count: 0,
        },
        {
          runtime_profile: 'enterprise-research',
          wall_duration_ms: 65_000,
          tool_call_count: 3,
          known_tool_duration_ms: 4_200,
        }
      ),
    ]
    useExecutionStore.getState().addRecord({ ...turn, events })
    renderWorkspace('replay')

    const workspace = screen.getByRole('region', { name: 'Execution workspace' })
    expect(within(workspace).getByText('Step 13 of 13')).toBeVisible()
    expect(within(workspace).getByText('Run metrics available')).toBeVisible()
    const summary = screen.getByRole('region', { name: 'Hermes run summary' })
    // The resolution, not the receipts: the uncited retrieval was not offered for citation
    expect(within(summary).getByText('1 cited evidence item(s)')).toBeVisible()
    expect(within(summary).getByText('1m 05s')).toBeVisible()
    expect(within(summary).getByText('3 tool call(s) · 4.2 s observed tool time')).toBeVisible()
    expect(
      within(summary).getByText(
        '103,009 input · 3,208 output · Runtime profile: enterprise-research'
      )
    ).toBeVisible()
    // Publishing completes the run's last stages, as the run's end does
    expect(document.querySelector('[data-node-id="trusted-answer"]')).toHaveAttribute(
      'data-state',
      'completed'
    )
  })

  it('steps through the run: a node opens only while its call is at the cursor', () => {
    serveDatabase()
    useExecutionStore.getState().addRecord(turn)
    renderWorkspace('replay')
    const position = screen.getByRole('slider', { name: 'Replay position' })

    fireEvent.change(position, { target: { value: '1' } })
    expect(screen.getByText('Step 1 of 10')).toBeVisible()
    expect(screen.getByText('Hermes Agent started')).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Hermes run summary' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Inspect Structured Retrieval' })).toBeNull()

    fireEvent.change(position, { target: { value: '3' } })
    expect(screen.getByText('Table query started')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Structured Retrieval' }))
    expect(
      screen.getByRole('dialog', { name: 'Structured Retrieval execution details' })
    ).toBeVisible()

    // Moving on leaves the explorer's call behind
    fireEvent.click(screen.getByRole('button', { name: 'Next execution step' }))
    expect(
      screen.getByRole('dialog', { name: 'Structured Retrieval replay status' })
    ).toHaveTextContent('Structured Retrieval is between observed calls')
  })

  it('opens on the evidence a citation points at, again for the same one, and for the next one', () => {
    serveDatabase()
    useExecutionStore.getState().addRecord(turn)
    const { rerender } = renderWorkspace('replay', {
      referenceId: receiptOf('retrieval_evidence').receiptId,
    })

    const details = 'Unstructured Retrieval execution details'
    const explorer = screen.getByRole('dialog', { name: details })
    expect(within(explorer).getByText(turn.question)).toBeVisible()
    expect(within(explorer).getByText('retail.policies')).toBeVisible()
    expect(within(explorer).getByText('Search query')).toBeVisible()
    expect(within(explorer).getByTestId('execution-evidence-output')).toHaveTextContent(
      'Northwind Retail Return Policy'
    )
    fireEvent.click(within(explorer).getByRole('button', { name: `Close ${details}` }))
    expect(screen.queryByRole('dialog', { name: details })).toBeNull()

    // The same citation, clicked again after closing its explorer
    rerender(
      <ExecutionWorkspace
        jobId={JOB}
        focus={{ referenceId: receiptOf('retrieval_evidence').receiptId }}
        onClose={vi.fn()}
      />
    )
    expect(screen.getByRole('dialog', { name: details })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: `Close ${details}` }))

    rerender(
      <ExecutionWorkspace
        jobId={JOB}
        focus={{ referenceId: receiptOf('structured_query').receiptId }}
        onClose={vi.fn()}
      />
    )
    expect(
      screen.getByRole('dialog', { name: 'Structured Retrieval execution details' })
    ).toBeVisible()
  })

  it('opens each node’s own explorer: agent, ontology lineage, Kumo and database', () => {
    serveDatabase()
    const [sqlTurn, predictionTurn] = (
      readRecording('sessions/gold-tier-and-churn.json') as { turns: ExecutionRecord[] }
    ).turns
    useExecutionStore.getState().addRecord(turn)
    useExecutionStore.getState().addRecord(sqlTurn)
    useExecutionStore.getState().addRecord(predictionTurn)

    renderWorkspace('replay')
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Hermes Agent' }))
    const agent = screen.getByRole('dialog', { name: 'Hermes Agent execution details' })
    expect(within(agent).getAllByTestId('execution-evidence-call')).toHaveLength(2)
    cleanup()

    render(<ExecutionWorkspace jobId={sqlTurn.jobId} focus={null} onClose={vi.fn()} />, {
      config: { mode: 'replay' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Inspect Auto Ontology' }))
    expect(screen.getByRole('dialog', { name: 'Auto Ontology text-to-SQL details' })).toBeVisible()
    // A bundle without a copy of the database: no query to open, and no database to browse
    expect(screen.queryByRole('button', { name: 'Open in Data Viewer' })).toBeNull()
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Inspect DuckDB Tables' }))
    expect(screen.getByRole('dialog', { name: 'Structured Database browser' })).toHaveTextContent(
      'No run-scoped structured database is available for this execution.'
    )
    cleanup()

    render(<ExecutionWorkspace jobId={predictionTurn.jobId} focus={null} onClose={vi.fn()} />, {
      config: { mode: 'replay' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Inspect NVIDIA Kumo' }))
    const kumo = screen.getByRole('dialog', { name: 'NVIDIA Kumo execution details' })
    expect(within(kumo).getByRole('heading', { name: 'NVIDIA Kumo Prediction' })).toBeVisible()
    expect(within(kumo).getByText('Generated PQL')).toBeVisible()
    expect(within(kumo).getByText('Template: churn_90d')).toBeVisible()
  })

  it('opens the data viewer in replay on the bundle’s copy of the database', async () => {
    const fetchMock = serveDatabase(readRecording('database.json'))
    const [sqlTurn] = (
      readRecording('sessions/gold-tier-and-churn.json') as { turns: ExecutionRecord[] }
    ).turns
    useExecutionStore.getState().addRecord(sqlTurn)
    render(<ExecutionWorkspace jobId={sqlTurn.jobId} focus={null} onClose={vi.fn()} />, {
      config: { mode: 'replay' },
    })

    fireEvent.click(screen.getByRole('button', { name: 'Inspect Auto Ontology' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Open in Data Viewer' }))
    const browser = screen.getByRole('dialog', { name: 'Structured Database browser' })
    fireEvent.click(await within(browser).findByRole('button', { name: 'Run query' }))
    const results = await within(browser).findByRole('region', { name: 'SQL results' })
    expect(results).toHaveTextContent('128.88')

    // A query the recording did not run cannot run without the API
    fireEvent.change(within(browser).getByLabelText('SQL'), { target: { value: 'SELECT 42' } })
    fireEvent.click(within(browser).getByRole('button', { name: 'Run query' }))
    expect(
      await within(browser).findByText(/Replay can rerun only the queries its recorded answers ran/)
    ).toBeVisible()
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/recordings/retail/database.json'])
  })

  it('opens a DuckDB table query in the data viewer from its explorer', async () => {
    serveDatabase(readRecording('database.json'))
    useExecutionStore.getState().addRecord({ ...turn, sourceIds: ['retail.sales'] } as ExecutionRecord)
    render(
      <ExecutionWorkspace
        jobId={JOB}
        focus={null}
        sourceIds={['retail.sales', 'retail.policies']}
        onClose={vi.fn()}
      />,
      { config: { mode: 'replay' } }
    )

    fireEvent.click(screen.getByRole('button', { name: 'Inspect Structured Retrieval' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Open in Data Viewer' }))
    const browser = screen.getByRole('dialog', { name: 'Structured Database browser' })
    fireEvent.click(await within(browser).findByRole('button', { name: 'Run query' }))
    const results = await within(browser).findByRole('region', { name: 'SQL results' })
    expect(results).toHaveTextContent('395')
  })

  it('loads a live run from the job export', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => Response.json(turn))
    renderWorkspace('live')

    expect(
      await screen.findByRole('button', { name: 'Inspect Unstructured Retrieval' })
    ).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith(`/api/v1/jobs/async/job/${JOB}/export`, {
      cache: 'no-store',
    })
  })

  it('labels a recorded run by its archive and job id, as the original did, and a live run by its job id', () => {
    serveDatabase()
    useExecutionStore
      .getState()
      .addRecord({ ...turn, recorded: true, archive: '20260928T060000Z-retail' })
    const { unmount } = renderWorkspace('replay')
    const label = `recorded:20260928T060000Z-retail:${JOB}`
    expect(screen.getByText(label)).toHaveAttribute('title', label)
    unmount()

    useExecutionStore.setState({ runs: {}, dropped: 0 })
    useExecutionStore.getState().addRecord(turn)
    renderWorkspace('replay')
    expect(screen.getByText(JOB)).toHaveAttribute('title', JOB)
  })

  it('in live mode, replays a recorded session’s run from the recording, not the API', async () => {
    const fetchMock = serveDatabase()
    useExecutionStore.getState().addRecord({ ...turn, recorded: true })
    renderWorkspace('live')

    const workspace = screen.getByRole('region', { name: 'Execution workspace' })
    expect(within(workspace).getByText('Hermes Recorded')).toBeVisible()
    expect(within(workspace).getByText('Step 10 of 10')).toBeVisible()
    // Only the bundle's copy of the database is asked for; there is no live job to export
    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/recordings/retail/database.json'])
    )
  })

  it('says so when a run has no execution record', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 404 }))
    renderWorkspace('live')
    expect(
      await screen.findByText('No execution record is available for this answer.')
    ).toBeVisible()
  })
})
