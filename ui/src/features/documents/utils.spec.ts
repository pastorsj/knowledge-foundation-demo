// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { getFileUploadConfigFromEnv } from '@/shared/config/file-upload'
import { describeAcceptedTypes, parserLabel, pipelineOf, pipelineSteps } from './utils'

describe('describeAcceptedTypes', () => {
  test('names the default types by kind, documents then tables', () => {
    const { acceptedTypes } = getFileUploadConfigFromEnv({} as NodeJS.ProcessEnv)
    expect(describeAcceptedTypes(acceptedTypes)).toBe(
      'PDF, images, Word, PowerPoint, HTML, Markdown, text · CSV, TSV, Excel, Parquet, JSON'
    )
  })

  test('lists only what is accepted, and an unknown extension as itself', () => {
    expect(describeAcceptedTypes('.csv, .PDF,.xml')).toBe('PDF · CSV, XML')
    expect(describeAcceptedTypes('.png')).toBe('images')
    expect(describeAcceptedTypes('')).toBe('')
  })
})

describe('the pipeline', () => {
  test('keeps the last stage before a failure, and shows the failed step there', () => {
    const embedding = { stage: 'embedding' } as Parameters<typeof pipelineOf>[0]
    const failed = { stage: 'failed' } as Parameters<typeof pipelineOf>[0]
    expect(pipelineOf(embedding).lastStage).toBe('embedding')
    expect(pipelineOf(failed)).not.toHaveProperty('lastStage')
    expect(pipelineSteps('document', 'failed', 'failed', 'embedding')).toEqual([
      { label: 'Parse', state: 'done' },
      { label: 'Chunk', state: 'done' },
      { label: 'Embed', state: 'failed' },
      { label: 'Index', state: 'pending' },
    ])
  })

  test('names the parsers', () => {
    expect(parserLabel('nemotron-parse-2.0')).toBe('Nemotron Parse 2.0')
    expect(parserLabel('docling-docx')).toBe('Docling DOCX')
    expect(parserLabel('duckdb-csv')).toBe('DuckDB CSV')
    expect(parserLabel(null)).toBeNull()
  })
})
