// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import type { FileUploadConfig } from '@/shared/context'

/** What the ingest service reads: documents (parsed) and tables (loaded into DuckDB) */
const DEFAULT_ACCEPTED_TYPES =
  '.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.docx,.pptx,.html,.md,.txt,.csv,.tsv,.xlsx,.parquet,.json,.jsonl'
const DEFAULT_MAX_SIZE_MB = 100
const DEFAULT_MAX_FILE_COUNT = 20

const EXTENSION_TO_MIME: Record<string, string[]> = {
  '.pdf': ['application/pdf'],
  '.png': ['image/png'],
  '.jpg': ['image/jpeg'],
  '.jpeg': ['image/jpeg'],
  '.tif': ['image/tiff'],
  '.tiff': ['image/tiff'],
  '.webp': ['image/webp'],
  '.md': ['text/markdown', 'text/x-markdown'],
  '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
  '.pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
  '.html': ['text/html'],
  '.txt': ['text/plain'],
  '.csv': ['text/csv'],
  '.tsv': ['text/tab-separated-values'],
  '.xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
  '.parquet': ['application/vnd.apache.parquet', 'application/octet-stream'],
  '.json': ['application/json'],
  '.jsonl': ['application/jsonl', 'application/x-ndjson'],
}

const parsePositiveNumber = (value: string | undefined): number | null => {
  if (!value) return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return null
  if (parsed <= 0) return null
  return parsed
}

export const buildAcceptedMimeTypes = (acceptedTypes: string): string[] => {
  const mimeTypes = new Set<string>()
  const extensions = acceptedTypes
    .split(',')
    .map((ext) => ext.toLowerCase().trim())
    .filter(Boolean)

  for (const ext of extensions) {
    const mimes = EXTENSION_TO_MIME[ext]
    if (!mimes) continue
    for (const mime of mimes) {
      mimeTypes.add(mime.toLowerCase())
    }
  }

  return Array.from(mimeTypes)
}

export const getFileUploadConfigFromEnv = (
  env: NodeJS.ProcessEnv = process.env
): FileUploadConfig => {
  const acceptedTypes = env.FILE_UPLOAD_ACCEPTED_TYPES || DEFAULT_ACCEPTED_TYPES
  const maxTotalSizeMB = parsePositiveNumber(env.FILE_UPLOAD_MAX_SIZE_MB) ?? DEFAULT_MAX_SIZE_MB
  const maxFileCount = parsePositiveNumber(env.FILE_UPLOAD_MAX_FILE_COUNT) ?? DEFAULT_MAX_FILE_COUNT
  const maxSizeBytes = maxTotalSizeMB * 1024 * 1024

  // Each file up to the size limit, each upload up to the count limit (the ingest service's
  // INGEST_MAX_FILE_MB and INGEST_MAX_FILES)
  return {
    acceptedTypes,
    acceptedMimeTypes: buildAcceptedMimeTypes(acceptedTypes),
    maxTotalSizeMB,
    maxFileSize: maxSizeBytes,
    maxTotalSize: Math.min(
      maxSizeBytes * maxFileCount,
      (parsePositiveNumber(env.FILE_UPLOAD_MAX_REQUEST_MB) ?? 512) * 1024 * 1024
    ),
    maxFileCount,
  }
}
