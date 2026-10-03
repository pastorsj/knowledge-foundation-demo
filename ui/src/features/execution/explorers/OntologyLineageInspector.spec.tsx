// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@/test-utils'
import type { StructuredQueryReceipt } from '../contract'
import type { ExecutionNodeDetail } from '../graph'
import { receiptOf } from '../test-utils/fixtures'
import { OntologyLineageInspector } from './OntologyLineageInspector'

const DETAIL: ExecutionNodeDetail = {
  id: 'nvidia-ontology',
  label: 'Auto Ontology',
  subtitle: 'Governed meaning',
  state: 'completed',
  observed: true,
  invocations: [],
  artifactRefs: [],
}

const call = (index: number, query: string): StructuredQueryReceipt => {
  const receipt = receiptOf('structured_query')
  return {
    ...receipt,
    receiptId: `receipt-${index}`,
    invocationId: `call-${index}`,
    content: {
      ...receipt.content!,
      query,
      sql: `SELECT ${index}`,
      resolutionLineage: [
        {
          phrase: 'cash dividend',
          ontologyObject: 'Action Type',
          table: 'corporate_actions',
          column: 'action_type',
        },
        {
          phrase: 'total return',
          ontologyObject: 'Total Return 1D',
          table: 'daily_prices',
          column: 'total_return_1d',
        },
      ],
    },
  }
}

const renderInspector = (
  receipts: StructuredQueryReceipt[],
  props: Partial<Parameters<typeof OntologyLineageInspector>[0]> = {}
) =>
  render(
    <OntologyLineageInspector
      detail={DETAIL}
      cursor="120"
      question="Which dividends were paid?"
      receipts={receipts}
      onClose={vi.fn()}
      {...props}
    />
  )

describe('OntologyLineageInspector', () => {
  it('walks from the phrases to ontology objects, columns and SQL', () => {
    renderInspector([call(1, 'Return every cash dividend')])

    const dialog = screen.getByRole('dialog', { name: 'Auto Ontology text-to-SQL details' })
    expect(within(dialog).getByText('Observed text-to-SQL lineage')).toBeVisible()
    expect(within(dialog).getByText('Return every cash dividend')).toBeVisible()
    expect(within(dialog).getByRole('region', { name: 'Resolved phrases' })).toHaveTextContent(
      'cash dividend → Action Type'
    )
    expect(within(dialog).getByRole('region', { name: 'Ontology matches' })).toHaveTextContent(
      'Total Return 1D'
    )
    expect(
      within(dialog).getByRole('region', { name: 'Tables, columns & joins' })
    ).toHaveTextContent('corporate_actions.action_type')
    expect(within(dialog).getByRole('region', { name: 'Generated SQL' })).toHaveTextContent(
      'SELECT 1'
    )
  })

  it('starts on the latest call, or the cited one, and switches between calls', () => {
    const receipts = [call(1, 'First try'), call(2, 'Repaired query')]
    const { unmount } = renderInspector(receipts)
    const selector = screen.getByRole('combobox', { name: 'Text-to-SQL call' })
    expect(selector).toHaveValue('receipt-2')
    expect(screen.getByText('Text-to-SQL call 2 of 2')).toBeVisible()
    fireEvent.change(selector, { target: { value: 'receipt-1' } })
    expect(screen.getByText('First try')).toBeVisible()
    unmount()

    renderInspector(receipts, { preferredInvocationId: 'call-1' })
    expect(screen.getByRole('combobox', { name: 'Text-to-SQL call' })).toHaveValue('receipt-1')
  })

  it('opens the SQL in the data viewer only for a database it can query', () => {
    const onOpenQuery = vi.fn()
    const receipt = call(1, 'Return every cash dividend')
    const { unmount } = renderInspector([receipt], { onOpenQuery, queryDatabases: [] })
    expect(screen.queryByRole('button', { name: 'Open in Data Viewer' })).toBeNull()
    unmount()

    renderInspector([receipt], { onOpenQuery, queryDatabases: ['market_analysis'] })
    fireEvent.click(screen.getByRole('button', { name: 'Open in Data Viewer' }))
    expect(onOpenQuery).toHaveBeenCalledWith(receipt)
  })

  it('says when no call was retained yet', () => {
    renderInspector([])
    expect(screen.getByText('Which dividends were paid?')).toBeVisible()
    expect(screen.getByText('No text-to-SQL call was retained at this replay step.')).toBeVisible()
  })
})
