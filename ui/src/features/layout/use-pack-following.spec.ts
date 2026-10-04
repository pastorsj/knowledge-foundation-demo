// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { useLayoutStore } from './store'
import { usePackFollowing } from './use-pack-following'

const router = { replace: vi.fn() }
let searchParams = new URLSearchParams()
vi.mock('next/navigation', () => ({
  useRouter: () => router,
  usePathname: () => '/research',
  useSearchParams: () => searchParams,
}))

const initialLayout = useLayoutStore.getState()

describe('usePackFollowing', () => {
  beforeEach(() => {
    useLayoutStore.setState({ ...initialLayout, packId: null, restoringPack: false }, true)
    searchParams = new URLSearchParams('pack=retail&question=q-1&session=s_1')
    document.cookie = 'kf-pack=; path=/; max-age=0'
    router.replace.mockClear()
  })

  afterEach(() => vi.restoreAllMocks())

  test('the pack the page names becomes the store’s, and the URL is left alone', () => {
    renderHook(() => usePackFollowing('retail'))

    expect(useLayoutStore.getState().packId).toBe('retail')
    expect(router.replace).not.toHaveBeenCalled()
  })

  test('a page naming another pack switches the store to it', () => {
    useLayoutStore.setState({ packId: 'retail' })
    const switchPack = vi.spyOn(useLayoutStore.getState(), 'switchPack').mockResolvedValue()

    renderHook(() => usePackFollowing('manufacturing'))

    expect(switchPack).toHaveBeenCalledWith('manufacturing')
    expect(router.replace).not.toHaveBeenCalled()
  })

  test('a pack the store switches to goes into the URL and the cookie, without the question or session', () => {
    useLayoutStore.setState({ packId: 'retail' })
    renderHook(() => usePackFollowing('retail'))

    act(() => useLayoutStore.setState({ packId: 'manufacturing' }))

    expect(router.replace).toHaveBeenLastCalledWith('/research?pack=manufacturing')
    expect(document.cookie).toContain('kf-pack=manufacturing')
  })

  test('a session being restored keeps its id in the URL; the question still goes', () => {
    useLayoutStore.setState({ packId: 'retail' })
    renderHook(() => usePackFollowing('retail'))

    act(() => useLayoutStore.setState({ packId: 'manufacturing', restoringPack: true }))

    expect(router.replace).toHaveBeenLastCalledWith('/research?pack=manufacturing&session=s_1')
  })
})
