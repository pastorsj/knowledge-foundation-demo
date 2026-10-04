// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test, vi } from 'vitest'
import { render, screen } from '@/test-utils'
import { DataConnectionCard } from './DataConnectionCard'

vi.mock('@/features/chat', () => ({ useIsCurrentSessionBusy: () => false }))

const card = (source: Parameters<typeof DataConnectionCard>[0]['source']) =>
  render(<DataConnectionCard source={source} isEnabled onToggle={vi.fn()} />)

describe('DataConnectionCard', () => {
  test('shows a table icon for a structured source and a document icon for documents', () => {
    const { container, unmount } = card({
      id: 'retail.sales',
      name: 'Sales & Customers',
      kind: 'structured',
    })
    expect(container.querySelector('svg[data-kind="table"]')).not.toBeNull()
    unmount()
    const documents = card({ id: 'retail.policies', name: 'Policies', kind: 'documents' })
    expect(documents.container.querySelector('svg[data-kind="doc"]')).not.toBeNull()
  })

  test.each([
    ['ingesting', 'Ingesting'],
    ['failed', 'Failed'],
    ['empty', 'Empty'],
  ])('says a %s source is not ready', (status, label) => {
    card({ id: 'workspace.tables', name: 'Your tables', kind: 'structured', status })
    expect(screen.getByTestId('source-status')).toHaveTextContent(label)
    expect(
      screen.getByRole('button', { name: `Your tables (${label.toLowerCase()}): enabled` })
    ).toBeVisible()
  })

  test('shows no chip for a ready source', () => {
    card({ id: 'retail.sales', name: 'Sales', kind: 'structured', status: 'ready' })
    expect(screen.queryByTestId('source-status')).toBeNull()
  })
})
