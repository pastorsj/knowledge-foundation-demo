// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@/test-utils'
import type { AnalyticsResultReceipt } from '../contract'
import { receiptOf } from '../test-utils/fixtures'
import { MarketToolExplorer } from './MarketToolExplorer'

const call = (index: number, device: 'cpu' | 'gpu'): AnalyticsResultReceipt => {
  const receipt = receiptOf('analytics_result')
  return {
    ...receipt,
    receiptId: `receipt-${index}`,
    invocationId: `call-${index}`,
    content: {
      ...receipt.content!,
      engine: {
        device,
        library: device === 'gpu' ? 'cuml.accel' : 'scikit-learn',
        version: '1',
        engineId: null,
      },
    },
  }
}

const renderExplorer = (
  receipts: AnalyticsResultReceipt[],
  preferredInvocationId: string | null = null
) => {
  const onClose = vi.fn()
  render(
    <MarketToolExplorer
      nodeId="market-anomaly-scan"
      title="Market Anomaly Scan"
      cursor="52057"
      receipts={receipts}
      preferredInvocationId={preferredInvocationId}
      onClose={onClose}
    />
  )
  return { onClose, dialog: screen.getByRole('dialog', { name: 'Market Anomaly Scan explorer' }) }
}

describe('MarketToolExplorer', () => {
  it('shows its one call with the replay cursor, and closes on Escape', () => {
    const { dialog, onClose } = renderExplorer([call(1, 'gpu')])

    expect(within(dialog).getByText('Market Analytics')).toBeVisible()
    expect(within(dialog).getByText('Replay cursor 52057')).toBeVisible()
    expect(within(dialog).getByTestId('capability-view-value')).toHaveTextContent(
      'Market Anomaly Scan callMarket Anomaly Scan Call 11 call available'
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('opens on the first GPU call, or the cited one, and switches between calls', () => {
    const receipts = [call(1, 'cpu'), call(2, 'gpu'), call(3, 'cpu')]
    const { dialog } = renderExplorer(receipts)
    const selector = within(dialog).getByRole('combobox', { name: 'Market Anomaly Scan call' })

    expect(selector).toHaveValue('receipt-2')
    expect(within(dialog).getByText('2 of 3 available')).toBeVisible()
    fireEvent.change(selector, { target: { value: 'receipt-3' } })
    expect(within(dialog).getByText('Tool execution receipt')).toBeVisible()
  })

  it('opens on the call a citation points at', () => {
    const { dialog } = renderExplorer([call(1, 'gpu'), call(2, 'cpu')], 'call-2')
    expect(within(dialog).getByRole('combobox')).toHaveValue('receipt-2')
  })

  it('says when the node has no call yet', () => {
    const { dialog } = renderExplorer([])
    expect(within(dialog).getByText('No Market Anomaly Scan call yet')).toBeVisible()
    expect(within(dialog).getByTestId('market-native-empty')).toBeVisible()
  })
})
