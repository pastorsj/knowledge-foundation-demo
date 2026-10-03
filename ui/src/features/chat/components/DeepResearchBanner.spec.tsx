// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import { describe, expect, test } from 'vitest'
import { DeepResearchBanner } from './DeepResearchBanner'

describe('DeepResearchBanner', () => {
  test.each([
    ['starting', 'Run started'],
    ['failure', 'Run failed'],
    ['cancelled', 'Run stopped'],
    ['expired', 'Run unavailable'],
  ] as const)('renders the %s banner with the run ID', (bannerType, heading) => {
    render(<DeepResearchBanner bannerType={bannerType} jobId="job-1" />)

    expect(screen.getByText(heading)).toBeInTheDocument()
    expect(screen.getByText(/Run ID: job-1/)).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})
