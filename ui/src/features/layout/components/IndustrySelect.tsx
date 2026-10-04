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

import { type CSSProperties, type FC, useCallback, useEffect, useId, useState } from 'react'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Flex, Select, Text } from '@/adapters/ui'
import { packIcon } from '@/adapters/ui/icons'
import { useChatStore, useIsCurrentSessionBusy } from '@/features/chat'
import type { PackSummary } from '@/generated/packs'
import { orderPacks, PACK_COOKIE, titleOfPackId } from '@/shared/config/packs'
import { useAppConfig } from '@/shared/context'
import { useLayoutStore } from '../store'

/** The label of a pack in the selector: the workspace is always "Your data". */
export const packLabel = (pack: Pick<PackSummary, 'kind' | 'title'>): string =>
  pack.kind === 'workspace' ? 'Your data' : pack.title

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
  const [packs, setPacks] = useState<PackSummary[]>(() => orderPacks(initial ?? []))
  const fetched = Boolean(initial?.length)
  useEffect(() => {
    if (fetched) return
    let active = true
    fetch(mode === 'live' ? '/api/v1/packs' : '/api/recordings/packs.json', { cache: 'no-store' })
      .then((response) => (response.ok ? response.json() : { packs: [] }))
      .then((body: { packs?: PackSummary[] }) => {
        if (active && Array.isArray(body.packs)) setPacks(orderPacks(body.packs))
      })
      .catch(() => undefined)
    return () => {
      active = false
    }
  }, [fetched, mode])
  return packs
}

const BUSY_HINT = 'The industry is fixed while an answer is running'

/** What the selector says of a pack that is not ready (the workspace is `empty` until an upload) */
const PACK_STATUS: Partial<Record<PackSummary['status'], string>> = {
  ingesting: 'Syncing…',
  failed: 'Failed',
}

/**
 * The menu is as wide as its longest industry, not as the trigger (which shows one short name), and
 * ends where the trigger ends, so it stays on screen at the right edge of the header.
 */
const MENU_STYLE: CSSProperties = {
  width: 'max-content',
  transform: 'translateX(calc(var(--popover-anchor-width) - 100%))',
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
  const busyHintId = useId()
  // The landing page follows its URL; the research page, the store (a restored session switches it)
  const selected = pathname === '/' ? packId : (storePack ?? packId)

  const selectPack = useCallback(
    (id: string) => {
      if (!id || id === selected) return
      rememberPack(id)
      // On the research page the layout follows the store's pack into the URL (usePackFollowing)
      if (pathname !== '/') {
        void useLayoutStore.getState().switchPack(id)
        return
      }
      const params = new URLSearchParams(searchParams?.toString())
      params.set('pack', id)
      params.delete('question')
      router.replace(`${pathname}?${params}`)
    },
    [pathname, router, searchParams, selected]
  )

  // A pack the list does not name (the API not answering yet) still shows as selected, by its name
  const items = packs.some((pack) => pack.id === selected)
    ? packs
    : [
        ...packs,
        {
          id: selected,
          kind: selected === 'workspace' ? 'workspace' : 'industry',
          title: titleOfPackId(selected),
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
            title: isBusy ? BUSY_HINT : 'Industry',
            // A disabled trigger takes no focus, so its title is never heard: say why in its description
            'aria-describedby': isBusy ? busyHintId : undefined,
          },
          SelectContent: { style: MENU_STYLE },
        }}
        items={items.map((pack) => {
          const Icon = packIcon(pack.icon)
          return {
            value: pack.id,
            children: (
              <Flex align="center" gap="2">
                <Icon className="h-4 w-4 shrink-0" width={16} height={16} />
                <Text kind="label/regular/md">{packLabel(pack)}</Text>
                {PACK_STATUS[pack.status] && (
                  <Text
                    kind="label/regular/sm"
                    className={pack.status === 'failed' ? 'text-error' : 'text-subtle'}
                    data-testid="pack-status"
                  >
                    {PACK_STATUS[pack.status]}
                  </Text>
                )}
              </Flex>
            ),
            attributes: {
              SelectItem: {
                'data-pack-id': pack.id,
                'data-pack-kind': pack.kind,
                title:
                  pack.status === 'ingesting'
                    ? 'Ingest is still syncing this pack: answers may miss some of its data'
                    : pack.status === 'failed'
                      ? 'This pack failed to sync: answers may miss its data'
                      : (pack.description ?? packLabel(pack)),
              },
            },
          }
        })}
      />
      {isBusy && (
        <span id={busyHintId} className="sr-only">
          {BUSY_HINT}
        </span>
      )}
    </div>
  )
}
