// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test } from 'vitest'
import { getFileUploadConfigFromEnv } from '@/shared/config/file-upload'
import {
  createEmptyValidationContext,
  isValidFileExtension,
  validateFileUpload,
} from './validation'

const MB = 1024 * 1024
const config = getFileUploadConfigFromEnv({
  FILE_UPLOAD_MAX_SIZE_MB: '1',
  FILE_UPLOAD_MAX_FILE_COUNT: '3',
} as unknown as NodeJS.ProcessEnv)

/** A file of `size` bytes, without allocating them */
const file = (name: string, size = 10) => {
  const made = new File(['x'], name)
  Object.defineProperty(made, 'size', { value: size })
  return made
}

describe('validateFileUpload', () => {
  test('passes accepted files within the limits', () => {
    const result = validateFileUpload([file('policy.pdf'), file('orders.csv')], undefined, config)
    expect(result).toMatchObject({ valid: true, canUpload: true, summary: null })
    expect(result.validFiles.map((f) => f.name)).toEqual(['policy.pdf', 'orders.csv'])
  })

  test('skips a file of a type the ingest service does not read, naming the types it does', () => {
    const result = validateFileUpload([file('setup.exe'), file('notes.md')], undefined, config)
    expect(result.canUpload).toBe(true)
    expect(result.validFiles.map((f) => f.name)).toEqual(['notes.md'])
    expect(result.fileErrors[0]).toMatchObject({ code: 'INVALID_TYPE' })
    expect(result.summary).toBe(
      '"setup.exe" is not a supported file type. Accepted: PDF, images, Word, PowerPoint, HTML, ' +
        'Markdown, text · CSV, TSV, Excel, Parquet, JSON'
    )
    expect(isValidFileExtension('REPORT.PDF', config)).toBe(true)
  })

  test('skips a file over the size limit', () => {
    const result = validateFileUpload([file('big.pdf', 2 * MB), file('ok.pdf')], undefined, config)
    expect(result.fileErrors).toEqual([
      expect.objectContaining({
        code: 'FILE_TOO_LARGE',
        message: '"big.pdf" is 2 MB, exceeds 1 MB limit',
      }),
    ])
    expect(result.validFiles.map((f) => f.name)).toEqual(['ok.pdf'])
  })

  test('refuses the whole upload past the file count', () => {
    const files = ['a.pdf', 'b.pdf', 'c.pdf', 'd.pdf'].map((name) => file(name))
    const result = validateFileUpload(files, undefined, config)
    expect(result).toMatchObject({ valid: false, canUpload: false, validFiles: [] })
    expect(result.batchErrors).toEqual([
      { code: 'MAX_FILES_EXCEEDED', message: '4 files exceeds the 3 file limit.' },
    ])
  })

  test('skips a name twice in one upload, and a name already in Your data', () => {
    const context = { ...createEmptyValidationContext(), existingFileNames: new Set(['old.csv']) }
    const result = validateFileUpload(
      [file('a.pdf'), file('a.pdf'), file('old.csv')],
      context,
      config
    )
    expect(result.validFiles.map((f) => f.name)).toEqual(['a.pdf'])
    expect(result.fileErrors.map((error) => error.message)).toEqual([
      '"a.pdf" is included multiple times',
      '"old.csv" is already in Your data',
    ])
    expect(result.summary).toBe('2 files have issues')
  })
})
