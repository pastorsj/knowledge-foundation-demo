// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * The pack a server-rendered page shows: the URL's `?pack=`, else the
 * `kf-pack` cookie the industry selector sets, else `DEFAULT_PACK`. A
 * malformed id is ignored.
 */

import { cookies } from 'next/headers'
import { isPackId, readDefaultPack } from '@/shared/config/env'
import { PACK_COOKIE } from '@/shared/config/packs'

export const selectedPack = async (param: unknown): Promise<string> => {
  if (isPackId(param)) return param
  const remembered = (await cookies()).get(PACK_COOKIE)?.value
  return isPackId(remembered) ? remembered : readDefaultPack()
}
