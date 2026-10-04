// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@/test-utils'
import { receiptOf } from '../test-utils/fixtures'
import { DatabaseQueryPanel } from './DatabaseQueryPanel'

const RESULT = {
  columns: [{ name: 'n', dataType: 'BIGINT' }],
  rows: [[null]],
  truncated: false,
  durationMs: 4,
}

describe('DatabaseQueryPanel', () => {
  it('highlights the SQL as it is typed', () => {
    render(<DatabaseQueryPanel sourceId="retail.sales" receipts={[]} defaultSql="" />)
    fireEvent.change(screen.getByRole('textbox', { name: 'SQL query' }), {
      target: { value: "SELECT count(*) FROM assets WHERE name = 'A' -- note" },
    })
    const layer = screen.getByTestId('sql-syntax-highlight')
    const kinds = [...layer.querySelectorAll('[data-sql-token]')].map((token) => [
      token.textContent,
      token.getAttribute('data-sql-token'),
    ])
    expect(kinds).toEqual(
      expect.arrayContaining([
        ['SELECT', 'keyword'],
        ['count', 'function'],
        ["'A'", 'string'],
        ['-- note', 'comment'],
      ])
    )
  })

  it('starts from an Auto Ontology call’s SQL and runs it with Cmd/Ctrl+Enter', async () => {
    const receipt = receiptOf('structured_query')
    const queryRunner = vi.fn().mockResolvedValue(RESULT)
    render(
      <DatabaseQueryPanel
        sourceId="retail.sales"
        receipts={[receipt]}
        initialReceiptId={receipt.receiptId}
        queryRunner={queryRunner}
      />
    )
    const editor = screen.getByRole('textbox', { name: 'SQL query' })
    expect(editor).toHaveValue(receipt.content!.sql)

    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    const results = await waitFor(() => screen.getByRole('region', { name: 'SQL results' }))
    expect(queryRunner).toHaveBeenCalledWith(
      'retail.sales',
      receipt.content!.sql,
      expect.anything()
    )
    expect(within(results).getByText('NULL')).toBeVisible()
    expect(within(results).getByText('BIGINT')).toBeVisible()
  })
})
