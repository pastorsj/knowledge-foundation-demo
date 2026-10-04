// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Document Feature Utilities
 *
 * Shared utility functions for status mapping and other common operations.
 */

import type { DocumentFileStatus, FileInfo, FileProgress, TrackedFile } from './types'
import type { FileSourceStatus } from '@/features/layout/components/FileSourceCard'

/**
 * Map backend file status string to DocumentFileStatus
 */
export const mapBackendStatus = (backendStatus: string): DocumentFileStatus => {
  const statusMap: Record<string, DocumentFileStatus> = {
    uploading: 'uploading',
    ingesting: 'ingesting',
    success: 'success',
    failed: 'failed',
  }
  return statusMap[backendStatus] || 'uploading'
}

/**
 * Map DocumentFileStatus to FileSourceCard display status
 */
export const mapToDisplayStatus = (status: string): FileSourceStatus => {
  switch (status) {
    case 'uploading':
      return 'uploading'
    case 'ingesting':
      return 'ingesting'
    case 'success':
      return 'available'
    case 'failed':
      return 'error'
    case 'deleting':
      return 'deleting'
    default:
      return 'uploading'
  }
}

/** The ingest pipeline's fields of a file, as a tracked file keeps them. */
export const pipelineOf = (
  file: FileInfo | FileProgress
): Pick<
  TrackedFile,
  'kind' | 'stage' | 'lastStage' | 'stageDetail' | 'parser' | 'tables' | 'warnings'
> => ({
  kind: file.kind ?? null,
  stage: file.stage ?? null,
  ...(file.stage && file.stage !== 'failed' && file.stage !== 'ready'
    ? { lastStage: file.stage }
    : {}),
  stageDetail: file.stage_detail ?? null,
  parser: file.parser ?? null,
  tables: file.tables ?? null,
  warnings: file.warnings ?? null,
})

/** A document's pipeline steps, and the stages each covers (detection precedes the first). */
const DOCUMENT_STEPS: ReadonlyArray<{ label: string; stages: readonly string[] }> = [
  { label: 'Parse', stages: ['received', 'detected', 'parsing', 'converting'] },
  { label: 'Chunk', stages: ['chunking'] },
  { label: 'Embed', stages: ['embedding'] },
  { label: 'Index', stages: ['indexing'] },
]
const TABLE_STEPS: ReadonlyArray<{ label: string; stages: readonly string[] }> = [
  { label: 'Load', stages: ['received', 'detected', 'loading'] },
  { label: 'Profile', stages: ['profiling'] },
]

export type StepState = 'done' | 'active' | 'pending' | 'failed'

/**
 * The stepper of a file's pipeline: document Parse → Chunk → Embed → Index, table Load → Profile.
 * A ready file has every step done; a failed one fails the step it stopped at (the first without a
 * known stage).
 */
export const pipelineSteps = (
  kind: TrackedFile['kind'],
  stage: string | null | undefined,
  status: TrackedFile['status'],
  /** The last stage before a failure, which the failed step is */
  lastStage?: string | null
): Array<{ label: string; state: StepState }> => {
  if (stage === 'failed') stage = lastStage ?? null
  const steps = kind === 'table' ? TABLE_STEPS : DOCUMENT_STEPS
  if (status === 'success' || stage === 'ready') {
    return steps.map(({ label }) => ({ label, state: 'done' }))
  }
  const current = Math.max(
    0,
    steps.findIndex((step) => stage && step.stages.includes(stage))
  )
  return steps.map(({ label }, index) => ({
    label,
    state:
      index < current
        ? 'done'
        : index > current
          ? 'pending'
          : status === 'failed'
            ? 'failed'
            : status === 'uploading'
              ? 'pending'
              : 'active',
  }))
}

const PARSER_LABELS: Readonly<Record<string, string>> = {
  'nemotron-parse-2.0': 'Nemotron Parse 2.0',
  'pdf-text-layer': 'PDF text layer',
}

/** The parser a file was read with, for people: "Nemotron Parse 2.0", "Docling DOCX", "DuckDB CSV". */
export const parserLabel = (parser: string | null | undefined): string | null => {
  if (!parser) return null
  if (PARSER_LABELS[parser]) return PARSER_LABELS[parser]
  const [engine, ...format] = parser.split('-')
  const name = engine === 'docling' ? 'Docling' : engine === 'duckdb' ? 'DuckDB' : engine
  return format.length ? `${name} ${format.join(' ').toUpperCase()}` : name
}

/**
 * Normalize backend filename to display name.
 * Backend returns filenames like "tmp_m04en5b_original-file.pdf"
 * This extracts "original-file.pdf" for display.
 *
 * Pattern: tmp_{random_id}_{original_filename}
 */
export const normalizeFileName = (backendFileName: string): string => {
  // Match pattern: tmp_{alphanumeric_id}_{rest}
  const match = backendFileName.match(/^tmp_[a-z0-9]+_(.+)$/i)
  if (match) {
    return match[1]
  }
  return backendFileName
}

/** What each extension is, for people, by group: documents (parsed), then tables (loaded into DuckDB) */
const TYPE_LABELS: ReadonlyArray<{
  group: 'documents' | 'tables'
  label: string
  extensions: string[]
}> = [
  { group: 'documents', label: 'PDF', extensions: ['.pdf'] },
  {
    group: 'documents',
    label: 'images',
    extensions: ['.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp'],
  },
  { group: 'documents', label: 'Word', extensions: ['.docx'] },
  { group: 'documents', label: 'PowerPoint', extensions: ['.pptx'] },
  { group: 'documents', label: 'HTML', extensions: ['.html', '.htm'] },
  { group: 'documents', label: 'Markdown', extensions: ['.md'] },
  { group: 'documents', label: 'text', extensions: ['.txt'] },
  { group: 'tables', label: 'CSV', extensions: ['.csv'] },
  { group: 'tables', label: 'TSV', extensions: ['.tsv'] },
  { group: 'tables', label: 'Excel', extensions: ['.xlsx', '.xls'] },
  { group: 'tables', label: 'Parquet', extensions: ['.parquet'] },
  { group: 'tables', label: 'JSON', extensions: ['.json', '.jsonl'] },
]

/**
 * The accepted types (`.pdf,.png,…`) as a short list people read, documents first, then tables:
 * "PDF, images, Word · CSV, Excel". An extension without a label is listed as itself (`XML`).
 */
export const describeAcceptedTypes = (acceptedTypes: string): string => {
  const extensions = acceptedTypes
    .split(',')
    .map((extension) => extension.trim().toLowerCase())
    .filter(Boolean)
  const labelled = (group: 'documents' | 'tables') =>
    TYPE_LABELS.filter(
      (type) => type.group === group && type.extensions.some((ext) => extensions.includes(ext))
    ).map((type) => type.label)
  const known = new Set(TYPE_LABELS.flatMap((type) => type.extensions))
  const others = extensions
    .filter((extension) => !known.has(extension))
    .map((extension) => extension.replace(/^\./, '').toUpperCase())
  return [labelled('documents'), [...labelled('tables'), ...others]]
    .filter((labels) => labels.length)
    .map((labels) => labels.join(', '))
    .join(' · ')
}
