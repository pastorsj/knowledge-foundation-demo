// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { render } from '@/test-utils'
import { Bank, Document, Factory, Heart, packIcon, Store, Upload } from './icons'

describe('packIcon', () => {
  test('maps the icons pack manifests name, and their aliases, case-insensitively', () => {
    expect(packIcon('Store')).toBe(Store)
    expect(packIcon('Factory')).toBe(Factory)
    expect(packIcon('Heart')).toBe(Heart)
    expect(packIcon('Hospital')).toBe(Heart)
    expect(packIcon('Bank')).toBe(Bank)
    expect(packIcon('wallet')).toBe(Bank)
    expect(packIcon(' upload ')).toBe(Upload)
  })

  test('gives an unknown or missing name a generic icon', () => {
    expect(packIcon('Spaceship')).toBe(Document)
    expect(packIcon(null)).toBe(Document)
    expect(packIcon(undefined)).toBe(Document)
  })

  test('loads each pack icon from the NVIDIA brand icon set', () => {
    const { container } = render(
      <>
        <Store />
        <Factory />
        <Bank />
      </>
    )
    expect(
      [...container.querySelectorAll('svg[data-src]')].map((svg) =>
        svg.getAttribute('data-src')?.split('/').at(-1)
      )
    ).toEqual(['shopping-cart.svg', 'factory.svg', 'bank.svg'])
  })
})
