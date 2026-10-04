// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

'use client'

import { useEffect, useRef } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { rememberPack } from './components/IndustrySelect'
import { useLayoutStore } from './store'

/**
 * Keeps the layout store's pack and the URL in step. A pack the page names (on load, or after a
 * navigation) becomes the store's; a pack the store switches to on its own (a restored session of
 * another industry) goes into the URL and the cookie, so the server renders that pack's examples.
 */
export const usePackFollowing = (packId: string): void => {
  const storePack = useLayoutStore((state) => state.packId)
  const named = useRef<string | null>(null)
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  useEffect(() => {
    const store = useLayoutStore.getState()
    if (named.current !== packId) {
      named.current = packId
      if (store.packId === null) store.setPackId(packId)
      else if (store.packId !== packId) void store.switchPack(packId)
      return
    }
    if (storePack && storePack !== packId) {
      rememberPack(storePack)
      // A question belongs to the pack it came from; so does a session, unless it is being restored
      const params = new URLSearchParams(searchParams?.toString())
      params.set('pack', storePack)
      params.delete('question')
      if (!useLayoutStore.getState().restoringPack) params.delete('session')
      router.replace(`${pathname}?${params}`)
    }
  }, [packId, pathname, router, searchParams, storePack])
}
