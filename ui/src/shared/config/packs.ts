// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * What the server-rendered pages and the industry selector share about the packs: the cookie that
 * remembers the selected one, and their order.
 */

import type { PackSummary } from '@/generated/packs'

/** The cookie the industry selector sets, so server-rendered pages follow the selected pack */
export const PACK_COOKIE = 'kf-pack'

/** Industries by title, then the workspace, as `GET /v1/packs` orders them. */
export const orderPacks = <T extends Pick<PackSummary, 'kind' | 'title'>>(
  packs: readonly T[]
): T[] =>
  [...packs].sort(
    (a, b) =>
      Number(a.kind === 'workspace') - Number(b.kind === 'workspace') ||
      a.title.localeCompare(b.title)
  )

/** A pack id as a title, for a pack the list does not name (`financial-services`: Financial Services). */
export const titleOfPackId = (id: string): string =>
  id
    .split('-')
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(' ')
