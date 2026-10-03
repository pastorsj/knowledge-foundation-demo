// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { getDataSourceKind } from './data-sources'

describe('getDataSourceKind', () => {
  test('maps web/search sources to the web kind', () => {
    expect(getDataSourceKind('web_search')).toBe('web')
    expect(getDataSourceKind('glean')).toBe('web')
  })

  test('maps everything else to the doc kind', () => {
    expect(getDataSourceKind('confluence')).toBe('doc')
    expect(getDataSourceKind('retail.sales')).toBe('doc')
  })
})
