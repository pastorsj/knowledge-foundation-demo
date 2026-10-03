// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { render, screen, waitFor } from '@/test-utils'
import type { PackSummary } from '@/generated/packs'
import { useChatStore } from '@/features/chat/store'
import { useLayoutStore } from '../store'
import { IndustrySelect, packLabel } from './IndustrySelect'

const replace = vi.fn()
let pathname = '/research'
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams('session=s_1&question=q-1'),
}))

const pack = (id: string, title: string, kind: PackSummary['kind'] = 'industry'): PackSummary => ({
  id,
  kind,
  title,
  description: null,
  icon: null,
  status: 'ready',
})
// As an older API might send them: unordered
const PACKS = [
  pack('workspace', 'Workspace', 'workspace'),
  pack('retail', 'Retail'),
  pack('manufacturing', 'Manufacturing'),
]

describe('IndustrySelect', () => {
  beforeEach(() => {
    pathname = '/research'
    document.cookie = 'kf-pack=; max-age=0; path=/'
    useLayoutStore.setState({ packId: 'retail' })
    useChatStore.setState({ isDeepResearchStreaming: false, currentConversation: null })
  })
  afterEach(() => vi.restoreAllMocks())

  test('lists the industries by title, then Your data, with the page’s pack selected', async () => {
    render(<IndustrySelect packId="retail" packs={PACKS} />)

    const trigger = screen.getByTestId('industry-select')
    expect(trigger).toHaveAccessibleName('Industry')
    expect(trigger).toHaveTextContent('Retail')
    await userEvent.click(trigger)
    const options = screen.getAllByRole('option')
    expect(options.map((option) => option.textContent)).toEqual([
      'Manufacturing',
      'Retail',
      'Your data',
    ])
    expect(options.map((option) => option.dataset.packKind)).toEqual([
      'industry',
      'industry',
      'workspace',
    ])
  })

  test('switches the pack: the store, the cookie and ?pack=, without the old question or session', async () => {
    const switchPack = vi.spyOn(useLayoutStore.getState(), 'switchPack').mockResolvedValue()
    render(<IndustrySelect packId="retail" packs={PACKS} />)

    await userEvent.click(screen.getByTestId('industry-select'))
    await userEvent.click(screen.getByRole('option', { name: 'Manufacturing' }))

    expect(switchPack).toHaveBeenCalledWith('manufacturing')
    expect(document.cookie).toContain('kf-pack=manufacturing')
    expect(replace).toHaveBeenCalledWith('/research?pack=manufacturing')
  })

  test('on the landing page, only the URL changes: the server renders the pack’s questions', async () => {
    pathname = '/'
    const switchPack = vi.spyOn(useLayoutStore.getState(), 'switchPack')
    render(<IndustrySelect packId="retail" packs={PACKS} />)

    await userEvent.click(screen.getByTestId('industry-select'))
    await userEvent.click(screen.getByRole('option', { name: 'Your data' }))

    expect(switchPack).not.toHaveBeenCalled()
    expect(replace).toHaveBeenCalledWith('/?pack=workspace')
  })

  test('is disabled while the current session has a question in flight', () => {
    useChatStore.setState({ isDeepResearchStreaming: true })
    render(<IndustrySelect packId="retail" packs={PACKS} />)
    expect(screen.getByTestId('industry-select')).toBeDisabled()
  })

  test('fetches the packs when the server listed none: the API live, the recordings in replay', async () => {
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockImplementation(async () => Response.json({ packs: PACKS }))
    const { unmount } = render(<IndustrySelect packId="retail" />)
    await userEvent.click(screen.getByTestId('industry-select'))
    await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(3))
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/packs', { cache: 'no-store' })
    unmount()

    render(<IndustrySelect packId="retail" />, { config: { mode: 'replay' } })
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/recordings/packs.json', { cache: 'no-store' })
    )
  })

  test('names the workspace Your data, whatever its title', () => {
    expect(packLabel({ kind: 'workspace', title: 'Workspace' })).toBe('Your data')
    expect(packLabel({ kind: 'industry', title: 'Healthcare' })).toBe('Healthcare')
  })
})
