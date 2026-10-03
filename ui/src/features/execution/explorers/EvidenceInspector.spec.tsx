// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@/test-utils'
import type { ReceiptV2 } from '../contract'
import type { ExecutionNodeDetail } from '../graph'
import { receiptOf } from '../test-utils/fixtures'
import { EvidenceInspector } from './EvidenceInspector'

const detail = (overrides: Partial<ExecutionNodeDetail> = {}): ExecutionNodeDetail => ({
  id: 'unstructured-retrieval',
  label: 'Unstructured Retrieval',
  subtitle: 'Candidate passages · reranking · evidence',
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
      question="Which filings report an outage?"
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
        'Candidate passages · reranking · evidence. Queries and display-safe results are shown directly below.'
      )
    ).toBeVisible()
    expect(within(dialog).getByText('Replay step 779')).toBeVisible()
    expect(within(dialog).getByText('Which filings report an outage?')).toBeVisible()
    expect(within(dialog).getByLabelText('Sources used')).toHaveTextContent('market_news')
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

  it('shows a Kumo prediction’s PQL and scores under its database, and numbers several calls', () => {
    renderInspector(
      [receiptOf('structured_prediction'), receiptOf('structured_prediction', 'failed')],
      {
        detail: detail({ id: 'nvidia-kumo', label: 'NVIDIA Kumo' }),
        databaseName: 'market_analysis',
        sourceIds: ['market_data', 'sec_filings'],
        structuredSources: [{ id: 'market_data', name: 'Market Prices & Events' }],
      }
    )
    // The question's structured source by name, then the database the prediction read
    const sources = screen.getByLabelText('Sources used')
    expect(within(sources).getByText('Sources used')).toBeVisible()
    expect([...sources.querySelectorAll('span')].map((chip) => chip.textContent)).toEqual([
      'Market Prices & Events',
      'market_analysis',
    ])
    const calls = screen.getAllByTestId('execution-evidence-call')
    expect(within(calls[0]).getByText('Recorded call 1 of 2')).toBeVisible()
    expect(within(calls[0]).getByRole('heading', { name: 'NVIDIA Kumo Prediction' })).toBeVisible()
    expect(within(calls[0]).getByText('Database: market_analysis')).toBeVisible()
    expect(within(calls[0]).getByText('Generated PQL')).toBeVisible()
    const output = within(calls[0]).getByTestId('execution-evidence-output')
    expect(
      within(output)
        .getAllByRole('columnheader')
        .map((th) => th.textContent)
    ).toEqual(['ANCHOR TIMESTAMP', 'ENTITY', 'FALSE PROB', 'PREDICTION', 'TRUE PROB'])
    expect(output).toHaveTextContent('asset-delta')
    expect(within(calls[1]).getByText('The tool call ended with a failure.')).toBeVisible()
    expect(within(calls[1]).getByText('Database: market_analysis')).toBeVisible()
  })

  it('shows Auto Ontology’s SQL and rows under the database it queried', () => {
    renderInspector([receiptOf('structured_query')], {
      detail: detail({ id: 'structured-retrieval', label: 'Structured Retrieval' }),
    })
    expect(screen.getByLabelText('Sources used')).toHaveTextContent('market_analysis')
    expect(screen.getByRole('heading', { name: 'Auto Ontology Text-to-SQL' })).toBeVisible()
    expect(screen.getByText('Generated SQL')).toBeVisible()
    expect(screen.getByText('Query result')).toBeVisible()
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
    expect(screen.getByRole('listitem')).toHaveTextContent('Unstructured Retrievalrunning')
  })
})
