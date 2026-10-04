// SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataConnectionCard Component
 *
 * Displays a single data source with an enable/disable toggle: a table icon
 * for a structured source, a document icon for a document collection, and a
 * chip when it is not ready (still ingesting, failed, or empty).
 */

'use client'

import { type FC } from 'react'
import { Flex, Text, Switch } from '@/adapters/ui'
import type { DataSourceFromAPI } from '@/adapters/api'
import { SourceKindIcon } from '@/shared/components/Sources/SourceKindIcon'
import { cn } from '@/shared/lib/cn'

interface DataConnectionCardProps {
  /** Data source from the API */
  source: DataSourceFromAPI
  /** Whether the source is currently enabled */
  isEnabled: boolean
  /** Whether the current session is busy with operations. Default: false */
  isBusy?: boolean
  /** Callback when toggle state changes */
  onToggle: (id: string, enabled: boolean) => void
}

/** The chip of a source that is not ready, and its color */
const STATUS_CHIPS: Record<string, { label: string; className: string; title: string }> = {
  ingesting: {
    label: 'Ingesting',
    className: 'text-info border-[var(--text-color-feedback-info)]',
    title:
      'Some of its files are still in the pipeline: answers may miss them until they are ready',
  },
  failed: {
    label: 'Failed',
    className: 'text-error border-[var(--text-color-feedback-danger)]',
    title: 'Its ingestion failed: answers miss it until it is synced again',
  },
  empty: {
    label: 'Empty',
    className: 'text-subtle border-base',
    title: 'It has no documents or tables yet',
  },
}

/**
 * Card component for displaying and controlling a data connection.
 */
export const DataConnectionCard: FC<DataConnectionCardProps> = ({
  source,
  isEnabled,
  isBusy = false,
  onToggle,
}) => {
  const chip = source.status ? STATUS_CHIPS[source.status] : undefined

  const handleToggle = (): void => {
    if (!isBusy) onToggle(source.id, !isEnabled)
  }

  return (
    <Flex
      align="center"
      justify="between"
      role="button"
      tabIndex={isBusy ? -1 : 0}
      onClick={handleToggle}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          handleToggle()
        }
      }}
      className={cn(
        'surface-card group flex items-center justify-between border p-3',
        isBusy
          ? 'border-base cursor-not-allowed opacity-50'
          : isEnabled
            ? 'brand-tint cursor-pointer border'
            : 'border-base hover:bg-surface-raised-50 cursor-pointer'
      )}
      aria-pressed={isEnabled}
      aria-disabled={isBusy}
      aria-label={`${source.name}${chip ? ` (${chip.label.toLowerCase()})` : ''}: ${isEnabled ? 'enabled' : 'disabled'}${isBusy ? ' (disabled)' : ''}`}
      title={isBusy ? 'Data source changes disabled during active operations' : undefined}
    >
      <Flex align="center" gap="3" className="min-w-0 flex-1">
        <span
          className={cn(
            'grid h-9 w-9 flex-shrink-0 place-items-center rounded-lg transition-colors',
            isBusy
              ? 'bg-surface-sunken text-subtle'
              : isEnabled
                ? 'brand-chip'
                : 'bg-surface-raised text-secondary'
          )}
        >
          <SourceKindIcon
            kind={source.kind === 'structured' ? 'table' : 'doc'}
            className="h-5 w-5"
          />
        </span>
        <Flex direction="col" className="min-w-0">
          <Flex align="center" gap="2" className="min-w-0">
            <Text
              kind="label/semibold/sm"
              className={cn('truncate', isBusy ? 'text-subtle' : 'text-primary')}
            >
              {source.name}
            </Text>
            {chip && (
              <span
                className={cn(
                  'shrink-0 rounded-full border px-1.5 text-[11px] font-semibold leading-4',
                  chip.className
                )}
                title={chip.title}
                data-testid="source-status"
              >
                {chip.label}
              </span>
            )}
          </Flex>
          <Text kind="body/regular/xs" className="text-subtle truncate">
            {source.description ?? ''}
          </Text>
        </Flex>
      </Flex>
      {/* Stop propagation to prevent a double toggle when clicking the switch directly */}
      <div className="ml-3 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
        <Switch
          size="small"
          checked={isEnabled}
          onCheckedChange={handleToggle}
          disabled={isBusy}
          aria-label={
            isBusy
              ? `${source.name} (disabled)`
              : `${isEnabled ? 'Disable' : 'Enable'} ${source.name}`
          }
        />
      </div>
    </Flex>
  )
}
