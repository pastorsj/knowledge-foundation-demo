// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'
import { SpeechInputButton } from './SpeechInputButton'

describe('SpeechInputButton', () => {
  test('announces and starts voice input from the keyboard', async () => {
    const user = userEvent.setup()
    const onToggle = vi.fn()
    render(<SpeechInputButton state="idle" onToggle={onToggle} />)

    const button = screen.getByRole('button', { name: 'Start voice input' })
    button.focus()
    await user.keyboard('{Enter}')

    expect(onToggle).toHaveBeenCalledOnce()
    expect(button).toHaveAttribute('aria-pressed', 'false')
  })

  test('uses a stop action while recording', () => {
    render(<SpeechInputButton state="recording" onToggle={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Stop voice recording' })).toHaveAttribute(
      'aria-pressed',
      'true'
    )
  })

  test('prevents another action while transcription is in progress', () => {
    render(<SpeechInputButton state="transcribing" onToggle={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'Transcribing speech' })).toBeDisabled()
  })
})
