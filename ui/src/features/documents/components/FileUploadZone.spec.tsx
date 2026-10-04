// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, expect, test, vi } from 'vitest'
import { render, screen } from '@/test-utils'
import { FileUploadZone } from './FileUploadZone'

const ACCEPTED =
  '.pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.docx,.pptx,.html,.md,.txt,.csv,.tsv,.xlsx,.parquet,.json,.jsonl'

describe('FileUploadZone', () => {
  test('says what it takes in words that fit the card, with the raw list in a title', () => {
    render(
      <FileUploadZone
        sessionId="workspace"
        acceptedTypes={ACCEPTED}
        maxFileSize={100 * 1024 * 1024}
        maxFileCount={20}
        onUpload={vi.fn()}
      />
    )
    expect(screen.getByText('Up to 20 files at a time, 100 MB each')).toBeVisible()
    const types = screen.getByTestId('accepted-types')
    expect(types).toHaveTextContent(
      'PDF, images, Word, PowerPoint, HTML, Markdown, text · CSV, TSV, Excel, Parquet, JSON'
    )
    // Wraps rather than running past the card
    expect(types.className).toMatch(/overflow-wrap:anywhere/)
    expect(types).toHaveAttribute('title', expect.stringContaining('.pdf, .png'))
    expect(screen.getByText(/drag and drop them here/)).toBeVisible()
  })

  test('hands the chosen files to the upload', async () => {
    const onUpload = vi.fn()
    const { container } = render(
      <FileUploadZone sessionId="workspace" acceptedTypes={ACCEPTED} onUpload={onUpload} />
    )
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['a,b\n1,2\n'], 'orders.csv', { type: 'text/csv' })
    Object.defineProperty(input, 'files', { value: [file] })
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await vi.waitFor(() => expect(onUpload).toHaveBeenCalledWith([file]))
  })
})
