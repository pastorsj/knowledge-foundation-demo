// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { type ReactNode, Suspense } from 'react'
import {
  fetchPack,
  fetchPacks,
  readRecordedPack,
  readRecordedPacks,
} from '@/adapters/api/pack-client'
import { CatalogNotice, EcosystemLanding } from '@/features/landing'
import { IndustrySelect } from '@/features/layout/components/IndustrySelect'
import { readUiMode } from '@/shared/config/env'
import { selectedPack } from './selected-pack'

interface HomePageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}

/**
 * Landing page, on the selected pack (`?pack=`, the `kf-pack` cookie or DEFAULT_PACK): its title,
 * its disclaimer and, in live mode, its featured questions once it is ready (Your data: an upload
 * card); the industry selector in the header. While the API has no catalog (ingest is syncing the
 * packs on first start, or the API is down) or the pack is still syncing, a notice says so and the
 * page renders again until it is ready. Replay mode reads the packs from their recordings bundles.
 */
const HomePage = async ({ searchParams }: HomePageProps): Promise<ReactNode> => {
  const packId = await selectedPack((await searchParams).pack)
  const live = readUiMode() === 'live'
  const [pack, packs] = live
    ? await Promise.all([fetchPack(packId), fetchPacks()])
    : await Promise.all([readRecordedPack(packId), readRecordedPacks()])
  const industry = pack?.kind !== 'workspace'
  const notice = !live ? null : !pack ? (
    <CatalogNotice
      title="The knowledge catalog is not ready yet"
      message="The API is starting, or ingest is still syncing the packs. This page tries again every few seconds."
    />
  ) : industry && pack.status === 'ingesting' ? (
    <CatalogNotice
      title={`Syncing ${pack.title}`}
      message="Ingest is still loading its documents and tables. Its featured questions appear once it is ready."
      refreshMs={10_000}
    />
  ) : industry && pack.status === 'failed' ? (
    <CatalogNotice
      title={`${pack.title} failed to sync`}
      message="Answers may miss its data. The ingest service's log says why (scripts/demo.sh logs ingest)."
      refreshMs={30_000}
    />
  ) : null
  return (
    <EcosystemLanding
      featuredQuestions={
        live && pack?.status === 'ready' ? pack.questions.filter((q) => q.featured) : []
      }
      notice={notice}
      workspace={live && pack?.kind === 'workspace'}
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
