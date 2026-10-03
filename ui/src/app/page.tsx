// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { type ReactNode, Suspense } from 'react'
import {
  fetchPack,
  fetchPacks,
  readRecordedPack,
  readRecordedPacks,
} from '@/adapters/api/pack-client'
import { EcosystemLanding } from '@/features/landing'
import { IndustrySelect } from '@/features/layout/components/IndustrySelect'
import { readUiMode } from '@/shared/config/env'
import { selectedPack } from './selected-pack'

interface HomePageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}

/**
 * Landing page, on the selected pack (`?pack=`, the `kf-pack` cookie or DEFAULT_PACK): its title,
 * its disclaimer and, in live mode, its featured questions; the industry selector in the header.
 * Replay mode reads the packs from their recordings bundles.
 */
const HomePage = async ({ searchParams }: HomePageProps): Promise<ReactNode> => {
  const packId = await selectedPack((await searchParams).pack)
  const live = readUiMode() === 'live'
  const [pack, packs] = live
    ? await Promise.all([fetchPack(packId), fetchPacks()])
    : await Promise.all([readRecordedPack(packId), readRecordedPacks()])
  return (
    <EcosystemLanding
      featuredQuestions={live ? (pack?.questions.filter((q) => q.featured) ?? []) : []}
      disclaimer={pack?.disclaimer ?? null}
      packId={packId}
      packTitle={pack ? (pack.kind === 'workspace' ? 'Your data' : pack.title) : null}
      industrySelect={
        <Suspense fallback={null}>
          <IndustrySelect packId={packId} packs={packs} />
        </Suspense>
      }
    />
  )
}

export default HomePage
