// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@/test-utils'
import type { ReceiptV2 } from '../contract'
import type { ExecutionNodeDetail } from '../graph'
import { receiptOf, receiptOfTool } from '../test-utils/fixtures'
import { EvidenceInspector } from './EvidenceInspector'

const detail = (overrides: Partial<ExecutionNodeDetail> = {}): ExecutionNodeDetail => ({
  id: 'unstructured-retrieval',
  label: 'Unstructured Retrieval',
  subtitle: 'Cited passages · evidence',
  state: 'completed',
  observed: true,
  invocations: [],
  artifactRefs: [],
  ...overrides,
})

const renderInspector = (
  receipts: ReceiptV2[],
  props: Partial<Parameters<typeof EvidenceInspector>[0]> = {}
) => {
  const onClose = vi.fn()
  const view = render(
    <EvidenceInspector
      detail={detail()}
      cursor="779"
      question="What is the return window for opened electronics?"
      receipts={receipts}
      onClose={onClose}
      {...props}
    />
  )
  return { ...view, onClose }
}

describe('EvidenceInspector', () => {
  it('shows the question, the sources and each recorded retrieval call', () => {
    const { onClose } = renderInspector([receiptOf('retrieval_evidence')])

    const dialog = screen.getByRole('dialog', { name: 'Unstructured Retrieval execution details' })
    expect(within(dialog).getByText('Observed execution details')).toBeVisible()
    expect(
      within(dialog).getByText(
        'Cited passages · evidence. Queries and display-safe results are shown directly below.'
      )
    ).toBeVisible()
    expect(within(dialog).getByText('Replay step 779')).toBeVisible()
    expect(
      within(dialog).getByText('What is the return window for opened electronics?')
    ).toBeVisible()
    expect(within(dialog).getByLabelText('Sources used')).toHaveTextContent('retail.policies')
    const call = within(dialog).getByTestId('execution-evidence-call')
    expect(within(call).getByText('Recorded call')).toBeVisible()
    expect(within(call).getByRole('heading', { name: 'Unstructured Retrieval' })).toBeVisible()
    expect(within(call).getByText('completed')).toBeVisible()
    expect(within(call).getByText('Retrieved passages')).toBeVisible()

    fireEvent.click(
      within(dialog).getByRole('button', { name: 'Close Unstructured Retrieval execution details' })
    )
    expect(onClose).toHaveBeenCalled()
  })

  it('shows a Kumo prediction’s PQL and scores under its source, and numbers several calls', () => {
    renderInspector(
      [receiptOf('structured_prediction'), receiptOf('structured_prediction', 'failed')],
      {
        detail: detail({ id: 'nvidia-kumo', label: 'NVIDIA Kumo' }),
        sourceIds: ['retail.sales', 'retail.policies'],
        structuredSources: [{ id: 'retail.sales', name: 'Sales' }],
      }
    )
    // The question's structured source by name, which the prediction read too
    const sources = screen.getByLabelText('Sources used')
    expect(within(sources).getByText('Source used')).toBeVisible()
    expect([...sources.querySelectorAll('span')].map((chip) => chip.textContent)).toEqual([
      'Sales',
    ])
    const calls = screen.getAllByTestId('execution-evidence-call')
    expect(within(calls[0]).getByText('Recorded call 1 of 2')).toBeVisible()
    expect(within(calls[0]).getByRole('heading', { name: 'NVIDIA Kumo Prediction' })).toBeVisible()
    expect(within(calls[0]).getByText('Source: retail.sales')).toBeVisible()
    expect(within(calls[0]).getByText('Template: churn_90d')).toBeVisible()
    expect(within(calls[0]).getByText('Generated PQL')).toBeVisible()
    const output = within(calls[0]).getByTestId('execution-evidence-output')
    expect(
      within(output)
        .getAllByRole('columnheader')
        .map((th) => th.textContent)
    ).toEqual(['ANCHOR TIMESTAMP', 'ENTITY', 'FALSE PROB', 'PREDICTION', 'TRUE PROB'])
    expect(output).toHaveTextContent('C2')
    expect(within(calls[1]).getByText('The tool call ended with a failure.')).toBeVisible()
    expect(
      within(calls[1]).getByText('No Kumo endpoint is configured (KUMO_RELATIONAL_URL).')
    ).toBeVisible()
  })

  it('shows a DuckDB table query’s SQL and rows under its database, and opens it in the data viewer', () => {
    const onOpenQuery = vi.fn()
    const { unmount } = renderInspector([receiptOf('structured_query')], {
      detail: detail({ id: 'structured-retrieval', label: 'Structured Retrieval' }),
      queryDatabases: ['retail_sales'],
      onOpenQuery,
    })
    expect(screen.getByLabelText('Sources used')).toHaveTextContent('retail_sales')
    expect(screen.getByRole('heading', { name: 'DuckDB Table Query' })).toBeVisible()
    expect(screen.getByText('Generated SQL')).toBeVisible()
    expect(screen.getByText('Query result')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Open in Data Viewer' }))
    expect(onOpenQuery).toHaveBeenCalledWith(receiptOf('structured_query'))
    unmount()

    // Auto Ontology's answers are named for it; a database the run cannot browse is not offered
    const ontology = receiptOfTool('ask_question')
    renderInspector([ontology], {
      detail: detail({ id: 'structured-retrieval', label: 'Structured Retrieval' }),
      onOpenQuery,
    })
    expect(screen.getByRole('heading', { name: 'Auto Ontology Text-to-SQL' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Open in Data Viewer' })).toBeNull()
  })

  it('says why there is no result: loading, or the calls seen so far', () => {
    const { unmount } = renderInspector([], { loading: true })
    expect(screen.getByRole('status')).toHaveTextContent('Loading the recorded query and result…')
    unmount()

    renderInspector([], {
      detail: detail({
        invocations: [
          {
            invocationId: 'call-1',
            name: 'retrieve_evidence',
            status: 'running',
            artifactRefs: [],
          },
        ],
      }),
    })
    expect(
      screen.getByText('No display-safe query result was retained at this replay step.')
    ).toBeVisible()
    expect(screen.getByRole('listitem')).toHaveTextContent('Document Retrievalrunning')
  })
})
