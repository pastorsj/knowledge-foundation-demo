// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, test, vi } from 'vitest'
import { render, screen } from '@/test-utils'
import { CatalogNotice } from './CatalogNotice'

const refresh = vi.fn()
vi.mock('next/navigation', () => ({ useRouter: () => ({ refresh }) }))

describe('CatalogNotice', () => {
  afterEach(() => vi.useRealTimers())

  test('says why, and renders the page again every few seconds until it is ready', () => {
    vi.useFakeTimers()
    const { unmount } = render(
      <CatalogNotice title="The knowledge catalog is not ready yet" message="Ingest is syncing." />
    )
    expect(screen.getByRole('status')).toHaveTextContent('The knowledge catalog is not ready yet')
    vi.advanceTimersByTime(5000)
    expect(refresh).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(10_000)
    expect(refresh).toHaveBeenCalledTimes(3)
    unmount()
    vi.advanceTimersByTime(10_000)
    expect(refresh).toHaveBeenCalledTimes(3)
  })
})
