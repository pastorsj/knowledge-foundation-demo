// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@/test-utils'
import type { StructuredQueryReceipt } from '../contract'
import { receiptOf } from '../test-utils/fixtures'
import { DatabaseBrowser } from './DatabaseBrowser'

const SOURCE = { id: 'market_data', name: 'Market data', databaseName: 'market_analysis' }

const SCHEMA = {
  source_id: 'market_data',
  database_name: 'market_analysis',
  tables: [
    {
      name: 'assets',
      schema: 'main',
      kind: 'table',
      primary_key: ['asset_id'],
      columns: [
        { name: 'asset_id', type: 'VARCHAR', nullable: false, primary_key: true },
        { name: 'name', type: 'VARCHAR', nullable: true, primary_key: false },
      ],
    },
    {
      name: 'daily_prices',
      schema: 'main',
      kind: 'table',
      primary_key: [],
      columns: [{ name: 'asset_id', type: 'VARCHAR', nullable: true, primary_key: false }],
    },
  ],
  relationships: [
    {
      from_table: 'daily_prices',
      from_column: 'asset_id',
      to_table: 'assets',
      to_column: 'asset_id',
    },
  ],
}

const rows = (value: string) => ({
  columns: ['asset_id'],
  types: ['VARCHAR'],
  rows: [[value]],
  truncated: false,
  duration_ms: 3,
})

/** The API's data viewer routes; a query that is not a SELECT is refused. */
const serveApi = () =>
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/schema')) return Response.json(SCHEMA)
    if (url.includes('/preview?table=')) return Response.json(rows('asset-delta'))
    const { sql } = JSON.parse(String(init?.body))
    return sql.startsWith('SELECT')
      ? Response.json(rows('asset-kestrel'))
      : Response.json({ detail: 'Only one SELECT query is supported.' }, { status: 422 })
  })

const renderBrowser = (props: Partial<Parameters<typeof DatabaseBrowser>[0]> = {}) => {
  const onClose = vi.fn()
  render(
    <DatabaseBrowser
      detail={{ id: 'structured-database' }}
      cursor="12"
      sources={[SOURCE]}
      onClose={onClose}
      {...props}
    />
  )
  return { onClose }
}

describe('DatabaseBrowser', () => {
  afterEach(() => vi.restoreAllMocks())

  it('lists the tables and shows the first one: columns, keys, relationships and rows', async () => {
    const fetchMock = serveApi()
    renderBrowser()

    const dialog = screen.getByRole('dialog', { name: 'Structured Database browser' })
    expect(within(dialog).getByText('Physical data source')).toBeVisible()
    expect(within(dialog).getByText('Market data')).toBeVisible()
    const rail = await screen.findByRole('complementary', { name: 'Database tables' })
    expect(within(rail).getByText('2 tables')).toBeVisible()
    expect(within(rail).getByRole('button', { name: /^main\s*assets/ })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
    const columns = screen.getByRole('region', { name: 'Table columns' })
    expect(within(columns).getByText('PK')).toBeVisible()
    expect(within(columns).getByText('Required')).toBeVisible()
    expect(screen.getByRole('region', { name: 'Table relationships' })).toHaveTextContent(
      'main.daily_prices.asset_id → main.assets.asset_id'
    )
    expect(await screen.findByTestId('database-sample-rows')).toHaveTextContent('asset-delta')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/data_sources/market_data/preview?table=assets&limit=8',
      expect.anything()
    )
  })

  it('runs read-only SQL, and shows the API’s reason when it refuses one', async () => {
    serveApi()
    renderBrowser()
    await userEvent.click(await screen.findByRole('button', { name: 'SQL Query' }))

    const editor = screen.getByRole('textbox', { name: 'SQL query' })
    expect(editor).toHaveValue('SELECT * FROM "main"."assets" LIMIT 25')
    await userEvent.click(screen.getByRole('button', { name: 'Run query' }))
    const results = await screen.findByRole('region', { name: 'SQL results' })
    expect(within(results).getByText('asset-kestrel')).toBeVisible()
    expect(within(results).getByText('VARCHAR')).toBeVisible()

    await userEvent.clear(editor)
    await userEvent.type(editor, 'DROP TABLE assets')
    await userEvent.click(screen.getByRole('button', { name: 'Run query' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Only one SELECT query is supported.'
    )
  })

  it('opens on an Auto Ontology call’s SQL', async () => {
    serveApi()
    const receipt: StructuredQueryReceipt = receiptOf('structured_query')
    renderBrowser({ receipts: [receipt], initialReceipt: receipt })
    expect(await screen.findByRole('textbox', { name: 'SQL query' })).toHaveValue(
      receipt.content!.sql
    )
  })

  it('says when the run has no structured database to browse', () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    renderBrowser({ sources: [] })
    expect(screen.getByText('No source selected')).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent(
      'No run-scoped structured database is available for this execution.'
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
