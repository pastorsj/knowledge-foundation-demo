// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Document API Schemas
 *
 * Zod schemas for runtime validation of document API responses.
 * All external data passes through these schemas at the adapter boundary.
 *
 * The ingest service adds optional pipeline fields to each file (`kind`,
 * `stage`, `stage_detail`, `parser`, `tables`, `warnings`); they are read when
 * present, and an upstream backend without them still validates.
 */

import { z } from 'zod'

// ============================================================================
// Enums
// ============================================================================

/** File processing status */
export const DocumentFileStatusSchema = z.enum(['uploading', 'ingesting', 'success', 'failed'])

/** Ingestion job state */
export const JobStateSchema = z.enum(['pending', 'processing', 'completed', 'failed'])

/** The pipeline fields the ingest service adds to a file (FileInfo and FileProgress) */
const PipelineFields = {
  /** `document` (parsed, chunked, embedded, indexed) or `table` (loaded, profiled) */
  kind: z.enum(['document', 'table']).nullish(),
  /** The stage it is at: received, detected, parsing, converting, loading, chunking, embedding, indexing, profiling, ready, failed */
  stage: z.string().nullish(),
  /** e.g. `page 3 of 12`, `sheet 2 of 3` */
  stage_detail: z.string().nullish(),
  /** What read it: nemotron-parse-2.0, pdf-text-layer, docling-docx, duckdb-csv, … */
  parser: z.string().nullish(),
  /** The tables a table file became */
  tables: z.array(z.string()).nullish(),
  warnings: z.array(z.string()).nullish(),
}

// ============================================================================
// Collection Schemas
// ============================================================================

/** Metadata about a collection/index */
export const CollectionInfoSchema = z.object({
  name: z.string(),
  description: z.string().nullable().optional(),
  file_count: z.number().default(0),
  chunk_count: z.number().default(0),
  created_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  backend: z.string().default(''),
  metadata: z.record(z.unknown()).default({}),
})

/** Response for list collections endpoint */
export const CollectionListResponseSchema = z.object({
  collections: z.array(CollectionInfoSchema),
})

// ============================================================================
// File Schemas
// ============================================================================

/** Metadata about a file within a collection */
export const FileInfoSchema = z.object({
  file_id: z.string(),
  file_name: z.string(),
  collection_name: z.string(),
  status: DocumentFileStatusSchema,
  file_size: z.number().nullable().optional(),
  chunk_count: z.number().default(0),
  uploaded_at: z.string().nullable().optional(),
  ingested_at: z.string().nullable().optional(),
  expiration_date: z.string().nullable().optional(),
  error_message: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).default({}),
  ...PipelineFields,
})

/** Response for list files endpoint */
export const FileListResponseSchema = z.object({
  files: z.array(FileInfoSchema),
})

/** Response for upload files endpoint */
export const UploadResponseSchema = z.object({
  job_id: z.string(),
  file_ids: z.array(z.string()),
  message: z.string().optional(),
})

// ============================================================================
// Job Status Schemas
// ============================================================================

/** Progress tracking for individual files within an ingestion job */
export const FileProgressSchema = z.object({
  file_id: z.string(),
  file_name: z.string(),
  status: DocumentFileStatusSchema,
  progress_percent: z.number(),
  error_message: z.string().nullable().optional(),
  chunks_created: z.number().nullish(),
  ...PipelineFields,
})

/** Status model for async ingestion jobs */
export const IngestionJobStatusSchema = z.object({
  job_id: z.string(),
  status: JobStateSchema,
  submitted_at: z.string(),
  started_at: z.string().nullable().optional(),
  completed_at: z.string().nullable().optional(),
  total_files: z.number(),
  processed_files: z.number(),
  file_details: z.array(FileProgressSchema),
  collection_name: z.string(),
  backend: z.string().default(''),
  error_message: z.string().nullable().optional(),
  metadata: z.record(z.unknown()).default({}),
})

// ============================================================================
// Type Exports
// ============================================================================

export type DocumentFileStatus = z.infer<typeof DocumentFileStatusSchema>
export type JobState = z.infer<typeof JobStateSchema>
export type CollectionInfo = z.infer<typeof CollectionInfoSchema>
export type CollectionListResponse = z.infer<typeof CollectionListResponseSchema>
export type FileInfo = z.infer<typeof FileInfoSchema>
export type FileListResponse = z.infer<typeof FileListResponseSchema>
export type UploadResponse = z.infer<typeof UploadResponseSchema>
export type FileProgress = z.infer<typeof FileProgressSchema>
export type IngestionJobStatus = z.infer<typeof IngestionJobStatusSchema>
