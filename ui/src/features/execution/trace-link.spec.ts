// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchTraceId, phoenixSpanUrl, phoenixTraceUrl } from './trace-link'

const PHOENIX = 'http://127.0.0.1:6006/'

describe('Phoenix links', () => {
  afterEach(() => vi.restoreAllMocks())

  it('links canonical trace and span ids through Phoenix redirects', () => {
    expect(phoenixTraceUrl(PHOENIX, '01a0e6627c447b83a27dc6ec13c18349')).toBe(
      'http://127.0.0.1:6006/redirects/traces/01a0e6627c447b83a27dc6ec13c18349'
    )
    expect(phoenixSpanUrl(PHOENIX, 'bcf375c672770770')).toBe(
      'http://127.0.0.1:6006/redirects/spans/bcf375c672770770'
    )
  })

  it('links nothing for a missing or malformed id', () => {
    expect(phoenixTraceUrl(PHOENIX, null)).toBeNull()
    expect(phoenixTraceUrl(PHOENIX, '../admin')).toBeNull()
    expect(phoenixSpanUrl(PHOENIX, 'BCF375C672770770')).toBeNull()
  })

  it('reads the job trace id the API resolved', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(Response.json({ trace_id: 'abc' }))
      .mockResolvedValueOnce(new Response(null, { status: 404 }))
    expect(await fetchTraceId('job 1')).toBe('abc')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/jobs/async/job/job%201/trace')
    expect(await fetchTraceId('job-2')).toBeNull()
  })
})
