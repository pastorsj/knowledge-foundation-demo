// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import { describe, expect, test } from 'vitest'
import { EcosystemLanding } from './EcosystemLanding'

const QUESTION = {
  id: 'returns-and-revenue',
  label: 'Returns and revenue',
  question: 'What is the electronics return window, and which tier brought the most revenue?',
  sources: ['retail.sales', 'retail.policies'],
  tools: [],
  featured: true,
}

describe('EcosystemLanding', () => {
  test('presents the architecture and enters the research view', () => {
    render(<EcosystemLanding featuredQuestions={[]} disclaimer={null} />)

    expect(
      screen.getByRole('heading', { name: 'Ask your enterprise knowledge.' })
    ).toBeInTheDocument()
    expect(screen.getByText('NVIDIA Knowledge Foundation')).toBeInTheDocument()
    expect(screen.getAllByTestId('ecosystem-node')).toHaveLength(6)
    expect(screen.getByRole('link', { name: /Ask your knowledge/ })).toHaveAttribute(
      'href',
      '/research'
    )
    expect(screen.queryByRole('heading', { name: 'Featured questions' })).not.toBeInTheDocument()
    // The pipeline every pack and upload goes through
    expect(screen.getByLabelText('Pipeline stages')).toHaveTextContent(
      'ParseChunkEmbedIndexLoad tables'
    )
    expect(screen.queryByText(/market/i)).not.toBeInTheDocument()
  })

  test('marks each technology with its logo or the NVIDIA mark', () => {
    const { container } = render(<EcosystemLanding featuredQuestions={[]} disclaimer={null} />)
    const brands = (selector: string) =>
      [...container.querySelectorAll(selector)].map((mark) => mark.getAttribute('data-brand'))

    expect(new Set(brands('[data-brand]:has(img)'))).toEqual(
      new Set([
        'Nous Research',
        'Docling',
        'DuckDB',
        'LangChain',
        'Milvus',
        'NVIDIA NIM',
        'OpenTelemetry',
        'Phoenix',
        'React',
        'Next.js',
        'FastAPI',
      ])
    )
    expect(container.querySelectorAll('[data-brand] img')).toHaveLength(13)
    for (const image of container.querySelectorAll('[data-brand] img')) {
      expect(image.getAttribute('src')).toMatch(/^\/ecosystem-logos\/[a-z]+\.(svg|png)$/)
      expect(image).toHaveAttribute('alt', '')
    }
    // Every mark without a logo file is the NVIDIA mark; no text badges remain.
    expect(new Set(brands('[data-brand]:not(:has(img))'))).toEqual(new Set(['NVIDIA']))
    const mark = (name: string) => screen.getByText(name).parentElement!.firstElementChild!
    expect(mark('NVIDIA Kumo')).toHaveAttribute('data-brand', 'NVIDIA')
    expect(mark('Docling')).toHaveAttribute('data-brand', 'Docling')
    expect(mark('NVIDIA Kumo').outerHTML).toBe(mark('Auto Ontology').outerHTML)
    for (const model of ['Nemotron Parse', 'Nemotron Embed', 'Nemotron Rerank']) {
      expect(mark(model)).toHaveAttribute('data-brand', 'NVIDIA NIM')
    }
    expect(container.querySelector('[data-brand="LangChain"] img')).toHaveAttribute(
      'src',
      '/ecosystem-logos/langchain.svg'
    )
    for (const name of ['OpenShell', 'Switchyard', 'Docling', 'DuckDB', 'Milvus', 'FastAPI']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    for (const gone of ['cuDF', 'cuGraph', 'cuML', 'RAPIDS']) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument()
    }
  })

  test('links each featured question to the research view on the selected pack', () => {
    render(
      <EcosystemLanding
        featuredQuestions={[QUESTION]}
        disclaimer="Synthetic data for a software demonstration."
        packId="retail"
        packTitle="Retail"
        industrySelect={<span data-testid="industry-select-slot" />}
      />
    )

    expect(screen.getByText('Synthetic data for a software demonstration.')).toBeInTheDocument()
    // The selector names the pack: the header does not name it again
    expect(screen.getByText('Architecture overview')).toBeInTheDocument()
    expect(screen.queryByText(/Architecture overview · /)).toBeNull()
    expect(screen.getByTestId('industry-select-slot')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Returns and revenue/ })
    expect(link).toHaveAttribute('href', '/research?pack=retail&question=returns-and-revenue')
    // The card may clamp the question; its full text stays in the link and its tooltip.
    expect(link).toHaveTextContent(QUESTION.question)
    expect(link).toHaveAttribute('title', QUESTION.question)
    expect(screen.getByRole('link', { name: /Ask your knowledge/ })).toHaveAttribute(
      'href',
      '/research?pack=retail'
    )
  })

  test('names the pack in the header when there is no selector', () => {
    render(<EcosystemLanding featuredQuestions={[]} disclaimer={null} packTitle="Retail" />)
    expect(screen.getByText('Architecture overview · Retail')).toBeInTheDocument()
  })

  test('in Your data, offers the upload as the next step', () => {
    render(
      <EcosystemLanding featuredQuestions={[]} disclaimer={null} packId="workspace" workspace />
    )
    expect(screen.getByRole('link', { name: /Upload documents and tables/ })).toHaveAttribute(
      'href',
      '/research?pack=workspace'
    )
  })

  test('shows a notice in place of the featured questions', () => {
    render(
      <EcosystemLanding
        featuredQuestions={[]}
        disclaimer={null}
        notice={<p role="status">The knowledge catalog is not ready yet</p>}
      />
    )
    expect(screen.getByRole('status')).toHaveTextContent('The knowledge catalog is not ready yet')
  })
})
