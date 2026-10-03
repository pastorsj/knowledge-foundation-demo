// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, it } from 'vitest'
import type { AnalyticsResultReceipt } from '../contract'
import { receiptOf } from '../test-utils/fixtures'
import { buildGpuAccelerationByNode, gpuAccelerationForReceipt } from './gpu-acceleration'

const anomaly = receiptOf('analytics_result')
const withEngine = (
  engine: NonNullable<AnalyticsResultReceipt['content']>['engine'],
  invocationId = anomaly.invocationId
): AnalyticsResultReceipt => ({
  ...anomaly,
  invocationId,
  content: { ...anomaly.content!, engine },
})

describe('buildGpuAccelerationByNode', () => {
  it('badges a node from a receipt that computed on the GPU with an NVIDIA library', () => {
    const badges = buildGpuAccelerationByNode([anomaly, receiptOf('retrieval_evidence')])
    expect([...badges.keys()]).toEqual(['market-anomaly-scan'])
    expect(badges.get('market-anomaly-scan')).toMatchObject({
      invocationCount: 1,
      technologies: [{ id: 'cuml', label: 'cuML', libraryVersion: '26.6.0' }],
    })
  })

  it('counts each call once and merges a technology across calls', () => {
    const second = withEngine(
      { device: 'gpu', library: 'cuml.accel', version: '26.6.0', engineId: null },
      'another-call'
    )
    const badge = buildGpuAccelerationByNode([anomaly, anomaly, second]).get('market-anomaly-scan')
    expect(badge?.invocationCount).toBe(2)
    expect(badge?.technologies).toHaveLength(1)
    expect(badge?.technologies[0].invocationCount).toBe(2)
  })

  it('never badges a CPU run, an unknown library or a failed receipt', () => {
    expect(
      gpuAccelerationForReceipt(
        withEngine({ device: 'cpu', library: 'pandas', version: '2', engineId: null })
      )
    ).toBeNull()
    expect(
      gpuAccelerationForReceipt(
        withEngine({ device: 'gpu', library: 'torch', version: '2', engineId: null })
      )
    ).toBeNull()
    expect(gpuAccelerationForReceipt({ ...anomaly, status: 'failed' })).toBeNull()
  })
})
