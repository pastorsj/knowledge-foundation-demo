// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'
import { useChatStore } from '@/features/chat'
import { useLayoutStore } from '../store'
import { InputArea } from './InputArea'

const hermes = vi.hoisted(() => ({ sendMessage: vi.fn(), stop: vi.fn() }))
const speech = vi.hoisted(() => ({
  transcribe: vi.fn(),
  recorder: { start: vi.fn(), stop: vi.fn(), cancel: vi.fn() },
}))

vi.mock('@/features/chat/hooks/use-hermes-chat', () => ({ useHermesChat: () => hermes }))
vi.mock('@/adapters/api/speech-client', () => ({ transcribeSpeech: speech.transcribe }))
vi.mock('@/features/speech-input/browser-recorder', () => ({
  createBrowserSpeechRecorder: () => speech.recorder,
}))

const VOICE = { speechInput: { enabled: true, maxSeconds: 60 } }

/** Eight examples, more than the five rows the picker shows */
const EXAMPLES = Array.from({ length: 8 }, (_, i) => ({
  id: `example-${i + 1}`,
  label: `Example ${i + 1}`,
  tools: ['duckdb' as const],
  description: `Example question ${i + 1}`,
  question: `Example question ${i + 1}?`,
  sourceIds: ['retail.sales'],
}))
const ROW_HEIGHT = 32

/** Lays the picker's options out in rows of ROW_HEIGHT px (happy-dom has no layout). */
const layOutOptions = () =>
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement
  ) {
    const row = [...document.querySelectorAll('[role="option"]')].indexOf(this)
    return row < 0
      ? new DOMRect(0, 0, 0, 0)
      : new DOMRect(0, 700 + row * ROW_HEIGHT, 380, ROW_HEIGHT)
  })

const initialChat = useChatStore.getState()
const initialLayout = useLayoutStore.getState()

describe('InputArea', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    useChatStore.setState(initialChat, true)
    useChatStore.getState().setCurrentUser('local')
    useLayoutStore.setState(
      {
        ...initialLayout,
        availableDataSources: [
          { id: 'retail.sales', name: 'Market data', kind: 'structured' },
          { id: 'retail.policies', name: 'Market news', kind: 'documents' },
        ],
        enabledDataSourceIds: ['retail.sales'],
      },
      true
    )
  })

  test('sends the question on Enter and clears the composer', async () => {
    render(<InputArea />)
    const input = screen.getByRole('textbox', { name: 'Chat message input' })

    await userEvent.type(input, 'Which assets led?{Enter}')

    expect(hermes.sendMessage).toHaveBeenCalledWith('Which assets led?')
    expect(input).toHaveValue('')
    expect(useChatStore.getState().currentConversation).not.toBeNull()
  })

  test('the example picker shows five rows and scrolls for the rest', async () => {
    layOutOptions()
    render(<InputArea scenarios={EXAMPLES} />)

    await userEvent.click(screen.getByTestId('demo-scenario-select'))
    const list = await screen.findByTestId('demo-scenario-list')
    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-scenario-id'))
    ).toEqual(EXAMPLES.map((example) => example.id))
    // Exactly five rows tall, measured from the rows, and never taller than the space it has
    await vi.waitFor(() =>
      expect(list.style.maxHeight).toBe(`min(${5 * ROW_HEIGHT}px, var(--max-height))`)
    )
  })

  test('the example picker scrolls the active row into view as the keyboard moves', async () => {
    const scrolled: Array<string | null> = []
    vi.spyOn(HTMLElement.prototype, 'scrollIntoView').mockImplementation(function (
      this: HTMLElement
    ) {
      scrolled.push(this.getAttribute('data-scenario-id'))
    })
    render(<InputArea scenarios={EXAMPLES} />)

    await userEvent.click(screen.getByTestId('demo-scenario-select'))
    await screen.findByTestId('demo-scenario-list')
    // Down to the sixth row, past the five the list shows: it scrolls into view, then the seventh
    await userEvent.keyboard('{ArrowDown}'.repeat(6))
    await vi.waitFor(() => expect(scrolled.at(-1)).toBe('example-6'))
    await userEvent.keyboard('{ArrowDown}')
    await vi.waitFor(() => expect(scrolled.at(-1)).toBe('example-7'))
    await userEvent.keyboard('{Enter}')
    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toHaveValue(
      'Example question 7?'
    )
  })

  test('disables sending an empty question', () => {
    render(<InputArea />)

    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled()
  })

  test('offers to stop while a run is in progress', async () => {
    useChatStore.setState({ isStreaming: true })
    render(<InputArea />)

    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Stop generating' }))
    expect(hermes.stop).toHaveBeenCalledOnce()
  })

  test('prefills the composer from a staged prompt', () => {
    useLayoutStore.setState({ promptDraft: 'A featured question' })
    render(<InputArea />)

    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toHaveValue(
      'A featured question'
    )
    expect(useLayoutStore.getState().promptDraft).toBeNull()
  })

  test('counts the enabled connections of the selected pack, as upstream did', () => {
    const { rerender } = render(<InputArea />)
    const counter = screen.getByRole('button', { name: 'Toggle data sources connections' })
    expect(counter).toHaveTextContent('1/2')

    // Documents count as much as tables
    useLayoutStore.setState({ enabledDataSourceIds: ['retail.sales', 'retail.policies'] })
    rerender(<InputArea />)
    expect(counter).toHaveTextContent('2/2')
  })

  test('has no microphone unless voice input is on', () => {
    render(<InputArea />)

    expect(screen.queryByRole('button', { name: 'Start voice input' })).toBeNull()
  })

  test('replay shows the read-only composer with a disabled microphone', () => {
    render(<InputArea />, { config: { mode: 'replay' } })

    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toHaveAttribute(
      'placeholder',
      'Recorded test sessions are read only'
    )
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeDisabled()
  })

  test('records a question and inserts its transcript where the cursor was', async () => {
    speech.recorder.start.mockResolvedValue(undefined)
    speech.recorder.stop.mockResolvedValue(new Blob([new Uint8Array(64)], { type: 'audio/wav' }))
    speech.recorder.cancel.mockResolvedValue(undefined)
    speech.transcribe.mockResolvedValue({ text: 'had the strongest returns' })
    render(<InputArea />, { config: VOICE })
    const input = screen.getByRole('textbox', { name: 'Chat message input' })
    await userEvent.type(input, 'Which assets over the summer?')
    ;(input as HTMLTextAreaElement).setSelectionRange(12, 12)

    await userEvent.click(screen.getByRole('button', { name: 'Start voice input' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Stop voice recording' }))

    await vi.waitFor(() =>
      expect(input).toHaveValue('Which assets had the strongest returns over the summer?')
    )
    expect(speech.transcribe).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: 'Start voice input' })).toBeEnabled()
  })

  test('shows a transcription failure without losing the draft', async () => {
    speech.recorder.start.mockResolvedValue(undefined)
    speech.recorder.stop.mockResolvedValue(new Blob([new Uint8Array(64)], { type: 'audio/wav' }))
    speech.transcribe.mockRejectedValue({
      userMessage: 'Voice transcription is busy. Wait a moment and try again.',
    })
    render(<InputArea />, { config: VOICE })
    await userEvent.type(screen.getByRole('textbox', { name: 'Chat message input' }), 'Draft')

    await userEvent.click(screen.getByRole('button', { name: 'Start voice input' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Stop voice recording' }))

    expect(
      await screen.findByText('Voice transcription is busy. Wait a moment and try again.')
    ).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'Chat message input' })).toHaveValue('Draft')
  })
})
