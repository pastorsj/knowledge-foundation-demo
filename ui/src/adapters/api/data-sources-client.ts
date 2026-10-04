// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Data Sources API Client
 *
 * Fetches the data sources of the selected pack: from the API
 * (`GET /v1/data_sources?pack=`) in live mode, or from the pack's recordings
 * bundle (`sources.json`, a copy of that same response) in replay mode.
 */

export interface DataSourceFromAPI {
  /** Unique identifier for the data source, namespaced by its pack (`retail.policies`) */
  id: string
  /** Display name for the source */
  name: string
  /** Brief description of the source */
  description?: string | null
  /** Whether the source starts enabled (defaults to true) */
  default_enabled?: boolean
  /** `structured` for a source of tables (one DuckDB database), `documents` for a document collection */
  kind?: 'structured' | 'documents'
  /** The structured source's DuckDB alias (`retail_sales`); null for document sources */
  database_name?: string | null
  /** The pack it belongs to; null for the workspace's sources in older catalogs */
  pack_id?: string | null
  /** `ready`; `ingesting` while some of its files are in the pipeline; `failed`; `empty` */
  status?: string | null
}

/** What a 503 means here: the API has no catalog until ingest has synced the packs (first start). */
export const CATALOG_BUILDING_MESSAGE =
  'The knowledge catalog is being built: ingest is syncing the packs. This page retries by itself.'

/** Answers worth trying again by themselves: the API or ingest is starting (or a proxy failed). */
const RETRYABLE_STATUSES: ReadonlySet<number> = new Set([502, 503, 504])

/** A data sources request the API refused, with its status; `retryable` when it may soon succeed. */
export class DataSourcesError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'DataSourcesError'
    this.status = status
  }

  get retryable(): boolean {
    return RETRYABLE_STATUSES.has(this.status)
  }
}

/**
 * Get the selected pack's data sources (every pack's without one).
 *
 * The API returns either a bare array or `{ data_sources: [...] }`.
 */
export const fetchDataSources = async (
  packId?: string | null,
  signal?: AbortSignal
): Promise<DataSourceFromAPI[]> => {
  const query = packId ? `?${new URLSearchParams({ pack: packId })}` : ''
  const response = await fetch(`/api/v1/data_sources${query}`, { signal })
  if (!response.ok) {
    // FastAPI answers `{detail}`; this UI's proxy, `{error: {message}}`
    const body = await response.json().catch(() => ({}))
    const detail = typeof body?.detail === 'string' ? body.detail : body?.error?.message
    throw new DataSourcesError(
      response.status === 503
        ? CATALOG_BUILDING_MESSAGE
        : detail || `Failed to fetch data sources: ${response.status}`,
      response.status
    )
  }
  const data = await response.json()
  return Array.isArray(data) ? data : (data.data_sources ?? [])
}

/** The data sources of a pack's replay bundle (`sources.json`, replay mode). */
export const fetchRecordedDataSources = async (
  packId: string,
  signal?: AbortSignal
): Promise<DataSourceFromAPI[]> => {
  const response = await fetch(`/api/recordings/${encodeURIComponent(packId)}/sources.json`, {
    signal,
  })
  if (!response.ok) throw new Error(`Failed to load the recorded data sources: ${response.status}`)
  const data: unknown = await response.json()
  const sources = Array.isArray(data) ? data : []
  return sources.filter(
    (source): source is DataSourceFromAPI =>
      typeof source === 'object' &&
      source !== null &&
      typeof (source as DataSourceFromAPI).id === 'string' &&
      typeof (source as DataSourceFromAPI).name === 'string'
  )
}
