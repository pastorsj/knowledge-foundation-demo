// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CatalogNotice: why the landing page has no featured questions yet (the API has no knowledge
 * catalog until ingest has synced the packs, or the selected pack is still syncing), refreshing the
 * server-rendered page until it has them.
 */

'use client'

import { type ReactNode, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import styles from './ecosystem-landing.module.css'

interface CatalogNoticeProps {
  title: string
  message: string
  /** How often the page renders again, in ms */
  refreshMs?: number
}

export const CatalogNotice = ({
  title,
  message,
  refreshMs = 5000,
}: CatalogNoticeProps): ReactNode => {
  const router = useRouter()
  useEffect(() => {
    const timer = setInterval(() => router.refresh(), refreshMs)
    return () => clearInterval(timer)
  }, [router, refreshMs])
  return (
    <section className={styles.notice} role="status" data-testid="catalog-notice">
      <strong>{title}</strong>
      <span>{message}</span>
    </section>
  )
}
