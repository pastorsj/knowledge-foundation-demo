// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * IndustrySelect Component
 *
 * The industry selector of the app bar and the landing header: the industries,
 * then "Your data" (the workspace of the user's uploads). The choice is the
 * URL's `?pack=` and the `kf-pack` cookie, so server-rendered pages (the
 * landing page's featured questions, the composer's examples) follow it. On
 * the research page it also switches the layout store's pack: its sources
 * replace the current ones and a new session draft starts. It is disabled
 * while the current session has a question in flight.
 */

'use client'

import { type FC, useCallback, useEffect, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Flex, Select, Text } from '@/adapters/ui'
import { packIcon } from '@/adapters/ui/icons'
import { useChatStore, useIsCurrentSessionBusy } from '@/features/chat'
import type { PackSummary } from '@/generated/packs'
import { useAppConfig } from '@/shared/context'
import { useLayoutStore } from '../store'

/** The cookie that remembers the selected pack between visits */
export const PACK_COOKIE = 'kf-pack'

/** The label of a pack in the selector: the workspace is always "Your data". */
export const packLabel = (pack: Pick<PackSummary, 'kind' | 'title'>): string =>
  pack.kind === 'workspace' ? 'Your data' : pack.title

/** Industries by title, then the workspace, as `GET /v1/packs` orders them. */
const ordered = (packs: readonly PackSummary[]): PackSummary[] =>
  [...packs].sort(
    (a, b) =>
      Number(a.kind === 'workspace') - Number(b.kind === 'workspace') ||
      a.title.localeCompare(b.title)
  )

/** Remembers the pack for the server-rendered pages. */
export const rememberPack = (packId: string): void => {
  document.cookie = `${PACK_COOKIE}=${encodeURIComponent(packId)}; path=/; max-age=31536000; samesite=lax`
}

/**
 * The packs a user can pick: the server's list when it rendered one, else fetched from the API
 * (live) or the recordings (replay).
 */
const usePacks = (initial: readonly PackSummary[] | undefined): PackSummary[] => {
  const { mode } = useAppConfig()
  const [packs, setPacks] = useState<PackSummary[]>(() => ordered(initial ?? []))
  const fetched = Boolean(initial?.length)
  useEffect(() => {
    if (fetched) return
    let active = true
    fetch(mode === 'live' ? '/api/v1/packs' : '/api/recordings/packs.json', { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : { packs: [] }))
      .then((body: { packs?: PackSummary[] }) => {
        if (active && Array.isArray(body.packs)) setPacks(ordered(body.packs))
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [fetched, mode])
  return packs
}

interface IndustrySelectProps {
  /** The pack the page shows */
  packId: string
  /** The packs, when the server already listed them */
  packs?: PackSummary[]
  className?: string
}

export const IndustrySelect: FC<IndustrySelectProps> = ({ packId, packs: initial, className }) => {
  const packs = usePacks(initial)
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  // Switching would leave a running answer without its pack: fixed while one streams
  const streaming = useChatStore((state) => state.isStreaming || state.isDeepResearchStreaming)
  const isBusy = useIsCurrentSessionBusy() || streaming
  const storePack = useLayoutStore((state) => state.packId)
  // The landing page follows its URL; the research page, the store (a restored session switches it)
  const selected = pathname === '/' ? packId : (storePack ?? packId)

  const selectPack = useCallback(
    (id: string) => {
      if (!id || id === selected) return
      rememberPack(id)
      // A question, or a session, belongs to the pack it came from
      const params = new URLSearchParams(searchParams?.toString())
      params.set('pack', id)
      params.delete('question')
      params.delete('session')
      if (pathname !== '/') void useLayoutStore.getState().switchPack(id)
      router.replace(`${pathname}?${params}`)
    },
    [pathname, router, searchParams, selected]
  )

  // A pack the list does not name yet (an older API) still shows as selected
  const items = packs.some((pack) => pack.id === selected)
    ? packs
    : [
        ...packs,
        {
          id: selected,
          kind: 'industry',
          title: selected,
          description: null,
          icon: null,
          status: 'ready',
        } satisfies PackSummary,
      ]

  return (
    <div className={className}>
      <Select
        aria-label="Industry"
        size="small"
        side="bottom"
        triggerKind="flat"
        value={selected}
        onValueChange={selectPack}
        disabled={isBusy}
        attributes={{
          SelectTrigger: {
            'data-testid': 'industry-select',
            title: isBusy ? 'The industry is fixed while an answer is running' : 'Industry',
          },
        }}
        items={items.map((pack) => {
          const Icon = packIcon(pack.icon)
          return {
            value: pack.id,
            children: (
              <Flex align="center" gap="2">
                <Icon className="h-4 w-4 shrink-0" width={16} height={16} />
                <Text kind="label/regular/md">{packLabel(pack)}</Text>
              </Flex>
            ),
            attributes: {
              SelectItem: {
                'data-pack-id': pack.id,
                'data-pack-kind': pack.kind,
                title: pack.description ?? packLabel(pack),
              },
            },
          }
        })}
      />
    </div>
  )
}
