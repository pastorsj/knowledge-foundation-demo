// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { render, screen, within } from '@/test-utils'
import { FileSourceCard } from './FileSourceCard'

const steps = () =>
  within(screen.getByRole('list', { name: /pipeline/ }))
    .getAllByRole('listitem')
    .map((step) => [step.textContent, step.dataset.state])

describe('FileSourceCard', () => {
  test('shows a document mid-pipeline: its stepper, its page, its progress and its parser', () => {
    render(
      <FileSourceCard
        id="f1"
        title="policy.pdf"
        status="ingesting"
        kind="document"
        stage="parsing"
        stageDetail="page 2 of 4"
        progress={35}
        parser="nemotron-parse-2.0"
        onDelete={vi.fn()}
      />
    )

    expect(screen.getByRole('list', { name: 'Document pipeline' })).toBeVisible()
    expect(steps()).toEqual([
      ['Parse: in progress', 'active'],
      ['Chunk: pending', 'pending'],
      ['Embed: pending', 'pending'],
      ['Index: pending', 'pending'],
    ])
    // The state is said, not only colored, and the current step is marked and announced
    expect(screen.getByRole('listitem', { current: 'step' })).toHaveTextContent('Parse')
    expect(screen.getByTestId('file-stage-announcement')).toHaveTextContent('policy.pdf: Parse')
    expect(screen.getByTestId('file-stage-announcement')).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByText('page 2 of 4')).toBeVisible()
    expect(
      screen.getByRole('progressbar', { name: 'policy.pdf ingestion progress' })
    ).toHaveAttribute('aria-valuenow', '35')
    expect(screen.getByTestId('file-parser')).toHaveTextContent('Nemotron Parse 2.0')
  })

  test('shows a PDF read from its text layer with the warning that says so', () => {
    render(
      <FileSourceCard
        id="f1"
        title="scan.pdf"
        status="available"
        kind="document"
        stage="ready"
        parser="pdf-text-layer"
        warnings={['Nemotron Parse was unavailable; the PDF text layer was used instead.']}
        onDelete={vi.fn()}
      />
    )
    expect(steps().every(([, state]) => state === 'done')).toBe(true)
    expect(screen.getByTestId('file-parser')).toHaveTextContent('PDF text layer')
    expect(screen.getByRole('note')).toHaveTextContent('the PDF text layer was used instead')
  })

  test('names a table file’s tables, each opening the data viewer', async () => {
    const onOpenTable = vi.fn()
    render(
      <FileSourceCard
        id="f2"
        title="orders.xlsx"
        status="available"
        kind="table"
        stage="ready"
        parser="duckdb-xlsx"
        tables={['orders_q3', 'orders_q4']}
        onOpenTable={onOpenTable}
        onDelete={vi.fn()}
      />
    )

    expect(screen.getByRole('list', { name: 'Table pipeline' })).toBeVisible()
    expect(steps()).toEqual([
      ['Load: done', 'done'],
      ['Profile: done', 'done'],
    ])
    expect(screen.getByTestId('file-stage-announcement')).toHaveTextContent(
      'orders.xlsx: Available'
    )
    expect(screen.getByTestId('file-parser')).toHaveTextContent('DuckDB XLSX')
    await userEvent.click(screen.getByRole('button', { name: 'Open orders_q4 in the data viewer' }))
    expect(onOpenTable).toHaveBeenCalledWith('orders_q4')
  })

  test('shows why a file failed, at the step it failed in', () => {
    render(
      <FileSourceCard
        id="f3"
        title="photo.png"
        status="error"
        kind="document"
        stage="parsing"
        errorMessage="Nemotron Parse is unavailable, and an image has no text layer to fall back to."
        onDelete={vi.fn()}
      />
    )
    expect(steps()[0]).toEqual(['Parse: failed', 'failed'])
    expect(screen.getByTestId('file-stage-announcement')).toHaveTextContent(
      'photo.png: failed at Parse'
    )
    expect(screen.getByText(/an image has no text layer/)).toBeVisible()
    expect(screen.queryByRole('progressbar')).toBeNull()
  })

  test('shows its delete button to a keyboard user, not only on hover', () => {
    render(
      <FileSourceCard
        id="f4"
        title="notes.md"
        status="available"
        kind="document"
        onDelete={vi.fn()}
      />
    )
    const remove = screen.getByRole('button', { name: 'Delete notes.md' })
    expect(remove.className).toMatch(/focus-visible:opacity-100/)
    expect(remove.className).toMatch(/group-focus-within:opacity-100/)
  })
})
