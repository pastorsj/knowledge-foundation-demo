// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, test, vi } from 'vitest'
import { VISIBLE_EXAMPLE_ROWS, visibleRowsHeight } from './visible-rows'

/** A list of `count` options of `rowHeight` px, laid out from y = 100 (happy-dom has no layout). */
const list = (count: number, rowHeight = 32, style = '') => {
  const element = document.createElement('div')
  element.setAttribute('style', style)
  element.innerHTML = Array.from({ length: count }, (_, i) => `<div role="option">${i}</div>`).join(
    ''
  )
  element.querySelectorAll<HTMLElement>('[role="option"]').forEach((option, i) => {
    const top = 100 + i * rowHeight
    vi.spyOn(option, 'getBoundingClientRect').mockReturnValue(
      DOMRect.fromRect({ x: 0, y: top, width: 300, height: rowHeight })
    )
  })
  document.body.append(element)
  return element
}

describe('visibleRowsHeight', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  test('the example picker shows five rows', () => {
    expect(VISIBLE_EXAMPLE_ROWS).toBe(5)
  })

  test('is the height of the first rows exactly, with the borders and top padding', () => {
    expect(visibleRowsHeight(list(12), 5)).toBe(160)
    expect(visibleRowsHeight(list(6, 36), 5)).toBe(180)
    const framed = list(8, 32, 'border: 1px solid; padding-top: 4px; padding-bottom: 4px')
    // No bottom padding: the sixth row starts right at the bottom edge
    expect(visibleRowsHeight(framed, 5)).toBe(160 + 1 + 4 + 1)
  })

  test('is null when every row fits, or before the list is laid out', () => {
    expect(visibleRowsHeight(list(5), 5)).toBeNull()
    expect(visibleRowsHeight(list(3), 5)).toBeNull()
    expect(visibleRowsHeight(list(8, 0), 5)).toBeNull()
  })
})
