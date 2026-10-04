// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FileSourceCard Component
 *
 * Displays a single uploaded file source with status and delete action.
 * Shows file title, upload time, description, and current status, and the
 * ingest pipeline's view of it: its stage stepper (document: Parse → Chunk →
 * Embed → Index; table: Load → Profile), the progress bar, the parser that read
 * it, its warnings and, for a table file, the tables it became, which open in
 * the data viewer.
 */

'use client'

import { type FC, useState, useEffect } from 'react'
import { Flex, Text, Button, Spinner } from '@/adapters/ui'
import { Document, Trash } from '@/adapters/ui/icons'
import { useIsCurrentSessionBusy } from '@/features/chat'
import { parserLabel, pipelineSteps, type StepState } from '@/features/documents'
import { cn } from '@/shared/lib/cn'

/** File source status types */
export type FileSourceStatus = 'uploading' | 'ingesting' | 'available' | 'error' | 'deleting'

export interface FileSourceCardProps {
  /** Unique identifier for the file */
  id: string
  /** File title/name */
  title: string
  /** File size in bytes */
  fileSize?: number | null
  /** When the file was uploaded (optional - not displayed if null/undefined) */
  uploadedAt?: Date | string | null
  /** Optional description of the file */
  description?: string
  /** Current status of the file */
  status: FileSourceStatus
  /** Error message when status is 'error' */
  errorMessage?: string
  /** Hours after upload before the file may expire (0 = no expiry shown) */
  expirationIntervalHours?: number
  /** `document` or `table`, once the pipeline has detected it */
  kind?: 'document' | 'table' | null
  /** The pipeline stage it is at, and its detail (`page 3 of 12`) */
  stage?: string | null
  /** The last stage before a failure */
  lastStage?: string | null
  stageDetail?: string | null
  /** Ingestion progress, 0 to 100 */
  progress?: number
  /** What read it (`nemotron-parse-2.0`, `pdf-text-layer`, `docling-docx`, `duckdb-csv`, …) */
  parser?: string | null
  /** The tables a table file became */
  tables?: string[] | null
  warnings?: string[] | null
  /** Opens a table in the data viewer */
  onOpenTable?: (table: string) => void
  /** Callback when delete is clicked */
  onDelete: (id: string) => void
}

/** Status configuration for styling */
const STATUS_CONFIG: Record<
  FileSourceStatus,
  { label: string; color: string; showSpinner: boolean }
> = {
  uploading: {
    label: 'Uploading...',
    color: 'var(--text-color-feedback-info)',
    showSpinner: true,
  },
  ingesting: {
    label: 'Ingesting...',
    color: 'var(--text-color-feedback-info)',
    showSpinner: true,
  },
  available: {
    label: 'Available',
    color: 'var(--text-color-feedback-success)',
    showSpinner: false,
  },
  error: {
    label: 'Error',
    color: 'var(--text-color-feedback-danger)',
    showSpinner: false,
  },
  deleting: {
    label: 'Deleting...',
    color: 'var(--text-color-subtle)',
    showSpinner: true,
  },
}

/** Pipeline step chip colors: done in brand green, the active step outlined, a failed one red */
const STEP_CLASSES: Record<StepState, string> = {
  done: 'brand-chip',
  active: 'border border-[var(--color-brand)] text-primary',
  pending: 'bg-surface-sunken text-subtle',
  failed: 'border border-[var(--text-color-feedback-danger)] text-error',
}

/**
 * Format byte count into a human-readable size string.
 */
const formatFileSize = (bytes: number): string => {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / Math.pow(1024, exponent)
  return `${value % 1 === 0 ? value : value.toFixed(1)} ${units[exponent]}`
}

/**
 * Format upload timestamp for display
 */
const formatDateTime = (date: Date | string): string => {
  const dateObj = typeof date === 'string' ? new Date(date) : date
  return dateObj.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Compute the milliseconds remaining until expiration.
 * Returns null if inputs are invalid or interval is 0.
 */
const computeMsRemaining = (
  uploadedAt: Date | string | null | undefined,
  intervalHours: number
): number | null => {
  if (!uploadedAt || intervalHours <= 0) return null
  const dateObj = typeof uploadedAt === 'string' ? new Date(uploadedAt) : uploadedAt
  if (isNaN(dateObj.getTime())) return null
  const expiresAtMs = dateObj.getTime() + intervalHours * 60 * 60 * 1000
  return expiresAtMs - Date.now()
}

/**
 * Format milliseconds remaining into "Expires in H:MM" or the expired label.
 * Returns null when expiration doesn't apply.
 */
const formatExpiryLabel = (
  msRemaining: number | null
): { text: string; expired: boolean } | null => {
  if (msRemaining === null) return null
  if (msRemaining <= 0) return { text: 'Deletion Pending - Reupload', expired: true }

  const totalMinutes = Math.max(1, Math.ceil(msRemaining / 60_000))
  return { text: `Expires in ${totalMinutes} min`, expired: false }
}

/**
 * Hook that returns a live expiry label, re-evaluated every minute.
 */
const useExpiryLabel = (
  uploadedAt: Date | string | null | undefined,
  intervalHours: number,
  active: boolean
): { text: string; expired: boolean } | null => {
  const [label, setLabel] = useState<{ text: string; expired: boolean } | null>(() =>
    active ? formatExpiryLabel(computeMsRemaining(uploadedAt, intervalHours)) : null
  )

  useEffect(() => {
    if (!active) {
      setLabel(null)
      return
    }

    setLabel(formatExpiryLabel(computeMsRemaining(uploadedAt, intervalHours)))

    const id = setInterval(() => {
      setLabel(formatExpiryLabel(computeMsRemaining(uploadedAt, intervalHours)))
    }, 60_000)

    return () => clearInterval(id)
  }, [uploadedAt, intervalHours, active])

  return label
}

/** The display status as the pipeline's file status, for its stepper */
const PIPELINE_STATUS = {
  uploading: 'uploading',
  ingesting: 'ingesting',
  available: 'success',
  error: 'failed',
  deleting: 'success',
} as const

/**
 * Card component for displaying an uploaded file source.
 */
export const FileSourceCard: FC<FileSourceCardProps> = ({
  id,
  title,
  fileSize,
  uploadedAt,
  description,
  status,
  errorMessage,
  expirationIntervalHours = 0,
  kind = null,
  stage = null,
  lastStage = null,
  stageDetail = null,
  progress = 0,
  parser = null,
  tables = null,
  warnings = null,
  onOpenTable,
  onDelete,
}) => {
  const config = STATUS_CONFIG[status]
  const isBusy = useIsCurrentSessionBusy()
  const expiryLabel = useExpiryLabel(uploadedAt, expirationIntervalHours, status === 'available')

  const handleDelete = () => {
    onDelete(id)
  }

  const isProcessing = status === 'uploading' || status === 'ingesting'
  const isDeleting = status === 'deleting'
  const deleteDisabled = isBusy || isProcessing || isDeleting
  const steps = pipelineSteps(kind, stage, PIPELINE_STATUS[status], lastStage)
  const reader = parserLabel(parser)
  const percent = Math.max(0, Math.min(100, Math.round(status === 'available' ? 100 : progress)))

  return (
    <Flex
      align="start"
      justify="between"
      data-testid="file-source-card"
      data-status={status}
      data-stage={stage ?? undefined}
      className={`
        bg-surface-raised border-base rounded-lg border
        p-3 transition-colors
        ${status === 'error' ? 'border-error/50' : ''}
        ${isDeleting ? 'opacity-50' : ''}
        group
      `}
    >
      <Flex align="start" gap="3" className="min-w-0 flex-1">
        {}
        {config.showSpinner ? (
          <Spinner size="small" aria-label={config.label} />
        ) : (
          <Document
            width={32}
            height={32}
            className={status === 'error' ? 'text-error' : 'text-secondary'}
          />
        )}

        {}
        <Flex direction="col" gap="1" className="min-w-0 flex-1">
          {}
          <Flex align="center" gap="2" className="min-w-0">
            <Text kind="label/semibold/sm" className="text-primary truncate">
              {title}
            </Text>
            {fileSize != null && fileSize > 0 && (
              <Text kind="body/regular/xs" className="text-subtle shrink-0">
                {formatFileSize(fileSize)}
              </Text>
            )}
            {uploadedAt && (
              <>
                <span className="text-subtle shrink-0">•</span>
                <Text kind="body/regular/xs" className="text-subtle shrink-0">
                  {formatDateTime(uploadedAt)}
                </Text>
              </>
            )}
          </Flex>

          {}
          {description && (
            <Text kind="body/regular/xs" className="text-subtle line-clamp-2">
              {description}
            </Text>
          )}

          {/* The pipeline: its steps, and how far the file is through them */}
          <ol
            className="mt-1 flex flex-wrap items-center gap-1"
            aria-label={`${kind === 'table' ? 'Table' : 'Document'} pipeline`}
          >
            {steps.map((step) => (
              <li
                key={step.label}
                data-state={step.state}
                className={cn(
                  'rounded-full px-2 py-0.5 text-[10px] font-semibold leading-4',
                  STEP_CLASSES[step.state]
                )}
              >
                {step.label}
              </li>
            ))}
          </ol>
          {(isProcessing || status === 'available') && (
            <div
              role="progressbar"
              aria-label={`${title} ingestion progress`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              className="bg-surface-sunken mt-1 h-1 w-full overflow-hidden rounded-full"
            >
              <div
                className="h-full rounded-full bg-[var(--color-brand)] transition-[width]"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}

          {}
          <Flex align="center" gap="2" className="mt-1">
            {}
            <Flex align="center" gap="1">
              {status === 'available' && <span className="text-success text-xs">✓</span>}
              {status === 'error' && <span className="text-error text-xs">✕</span>}
              <Text
                kind={config.showSpinner ? 'body/regular/sm' : 'body/regular/xs'}
                style={{ color: config.color }}
              >
                {config.label}
              </Text>
            </Flex>
            {isProcessing && stageDetail && (
              <Text kind="body/regular/xs" className="text-subtle">
                {stageDetail}
              </Text>
            )}
            {reader && (
              <>
                <span className="text-subtle">•</span>
                <Text kind="body/regular/xs" className="text-subtle" data-testid="file-parser">
                  {reader}
                </Text>
              </>
            )}

            {}
            {expiryLabel && (
              <>
                <span className="text-subtle">•</span>
                <Text
                  kind="body/regular/xs"
                  className={expiryLabel.expired ? 'text-warning' : 'text-orange-400'}
                >
                  {expiryLabel.text}
                </Text>
              </>
            )}
          </Flex>

          {warnings?.map((warning, index) => (
            <Text
              key={`${index}:${warning}`}
              kind="body/regular/xs"
              className="text-warning"
              role="note"
            >
              {warning}
            </Text>
          ))}

          {tables && tables.length > 0 && (
            <Flex align="center" gap="1" className="flex-wrap">
              <Text kind="body/regular/xs" className="text-subtle">
                {tables.length === 1 ? 'Table' : 'Tables'}:
              </Text>
              {tables.map((table) =>
                onOpenTable && status === 'available' ? (
                  <button
                    key={table}
                    type="button"
                    className="text-brand cursor-pointer font-mono text-xs underline-offset-2 hover:underline"
                    onClick={() => onOpenTable(table)}
                    aria-label={`Open ${table} in the data viewer`}
                  >
                    {table}
                  </button>
                ) : (
                  <code key={table} className="text-primary text-xs">
                    {table}
                  </code>
                )
              )}
            </Flex>
          )}

          {}
          {status === 'error' && errorMessage && (
            <Text kind="body/regular/xs" className="text-error mt-1">
              {errorMessage}
            </Text>
          )}
        </Flex>

        {}
        <Button
          kind="tertiary"
          size="small"
          color="danger"
          onClick={handleDelete}
          disabled={deleteDisabled}
          aria-label={deleteDisabled ? `Delete ${title} (disabled)` : `Delete ${title}`}
          title={
            isProcessing
              ? 'Wait for upload to complete'
              : deleteDisabled
                ? 'Cannot delete files during active operations'
                : 'Delete file'
          }
          className="ml-2 flex-shrink-0 opacity-0 transition-opacity group-hover:opacity-100"
        >
          <Trash width={16} height={16} className="text-subtle hover:text-error" />
        </Button>
      </Flex>
    </Flex>
  )
}
