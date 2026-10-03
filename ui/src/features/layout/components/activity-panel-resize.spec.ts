// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import {
  activityPanelWidthBounds,
  clampActivityPanelWidth,
  defaultActivityPanelWidth,
} from './activity-panel-resize'

describe('activity panel resize geometry', () => {
  test('reserves answer space while keeping the normal activity minimum', () => {
    expect(activityPanelWidthBounds(1440)).toEqual({ min: 400, max: 1080 })
    expect(activityPanelWidthBounds(800)).toEqual({ min: 400, max: 440 })
  })

  test('collapses the minimum gracefully in a constrained container', () => {
    expect(activityPanelWidthBounds(700)).toEqual({ min: 340, max: 340 })
    expect(activityPanelWidthBounds(-100)).toEqual({ min: 40, max: 40 })
  })

  test('uses a stable fallback for an unavailable container measurement', () => {
    expect(activityPanelWidthBounds(Number.NaN)).toEqual({ min: 400, max: 1080 })
    expect(activityPanelWidthBounds(Number.POSITIVE_INFINITY)).toEqual({ min: 400, max: 1080 })
  })

  test('clamps and rounds requested widths to the available bounds', () => {
    const bounds = { min: 400, max: 1080 }

    expect(clampActivityPanelWidth(320, bounds)).toBe(400)
    expect(clampActivityPanelWidth(750.6, bounds)).toBe(751)
    expect(clampActivityPanelWidth(1200, bounds)).toBe(1080)
  })

  test('preserves the existing presentation as its responsive default', () => {
    expect(defaultActivityPanelWidth(1440)).toBe(904)
    expect(defaultActivityPanelWidth(800)).toBe(440)
    expect(defaultActivityPanelWidth(700)).toBe(340)
  })
})
