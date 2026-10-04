// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Research Page
 *
 * The chat experience, on the selected pack (`?pack=`, the `kf-pack` cookie or
 * DEFAULT_PACK). In live mode the pack's examples are the composer's demo
 * scenarios, and `?question=<id>` (from the landing page) places that
 * question, any of the pack's, in the composer.
 */

import { type ReactNode, Suspense } from 'react'
import { fetchPack, fetchPacks, readRecordedPacks } from '@/adapters/api/pack-client'
import { MainLayout, toDemoScenarios, type InitialQuestion } from '@/features/layout'
import { readUiMode } from '@/shared/config/env'
import { selectedPack } from '../selected-pack'

interface ResearchPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>
}

const ResearchPage = async ({ searchParams }: ResearchPageProps): Promise<ReactNode> => {
  const params = await searchParams
  const packId = await selectedPack(params.pack)
  const live = readUiMode() === 'live'
  // The selector's packs come with the page, so it never shows a pack by its bare id
  const [pack, packs] = live
    ? await Promise.all([fetchPack(packId), fetchPacks()])
    : [null, await readRecordedPacks()]
  const questionId = params.question
  const question =
    typeof questionId === 'string' ? pack?.questions.find((q) => q.id === questionId) : undefined
  const initialQuestion: InitialQuestion | null = question
    ? { question: question.question, sourceIds: question.sources }
    : null

  // MainLayout reads the ?session= and ?pack= parameters, which need a Suspense boundary.
  return (
    <Suspense fallback={null}>
      <MainLayout
        packId={packId}
        packs={packs}
        initialQuestion={initialQuestion}
        demoScenarios={toDemoScenarios(pack?.questions ?? [], pack?.examples)}
      />
    </Suspense>
  )
}

export default ResearchPage
