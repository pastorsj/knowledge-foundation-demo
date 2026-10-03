// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { vi, describe, test, expect } from 'vitest'
import { AgentResponse } from './AgentResponse'

const ANSWER_WITH_EVIDENCE =
  'Leaders were stable [1].\n\n**References:**\n' +
  '- [1] Market analytics result — market scan — evidence `ev-1` — invocation `call_7`'

describe('AgentResponse', () => {
  test('renders response content', () => {
    render(<AgentResponse content="Here is your answer" />)

    expect(screen.getByText('Here is your answer')).toBeInTheDocument()
  })

  test.each(['', '   ', 'null'])('renders nothing for empty content %j', (content) => {
    const { container } = render(<AgentResponse content={content} />)

    expect(container.querySelector('.agent-final-response')).toBeNull()
  })

  test('displays the timestamp when provided', () => {
    render(<AgentResponse content="Response" timestamp="2024-01-15T14:30:00Z" />)

    expect(screen.getByText(/\d{1,2}:\d{2}/)).toBeInTheDocument()
  })

  test('strips a baked references block from the body and lists the sources', () => {
    render(
      <AgentResponse
        content={'Revenue rose [1].\n\n**References:**\n- [1] Q4 - https://a.example.com'}
      />
    )

    expect(screen.queryByText(/\*\*References/)).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Sources' })).toBeInTheDocument()
    expect(screen.getByText('a.example.com')).toBeInTheDocument()
  })

  test('opens cited run evidence through the execution view callback', async () => {
    const onOpenEvidence = vi.fn()
    render(<AgentResponse content={ANSWER_WITH_EVIDENCE} onOpenEvidence={onOpenEvidence} />)

    await userEvent.click(
      screen.getByRole('button', { name: 'Open this run in the execution view' })
    )

    expect(onOpenEvidence).toHaveBeenCalledWith({ referenceId: 'ev-1', invocationId: 'call_7' })
  })

  test('copies the original content including references', async () => {
    const user = userEvent.setup()
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    render(<AgentResponse content={ANSWER_WITH_EVIDENCE} />)

    await user.click(screen.getByRole('button', { name: 'Copy answer' }))

    expect(writeText).toHaveBeenCalledWith(ANSWER_WITH_EVIDENCE)
  })
})
