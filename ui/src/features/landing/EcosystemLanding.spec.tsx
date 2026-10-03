// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { render, screen } from '@/test-utils'
import { describe, expect, test } from 'vitest'
import { EcosystemLanding } from './EcosystemLanding'

const QUESTION = {
  id: 'market-leaders',
  label: 'Market Leaders',
  question: 'Which assets had the strongest returns?',
  sources: ['market_analysis_structured'],
  tools: [],
  featured: true,
}

describe('EcosystemLanding', () => {
  test('presents the architecture and enters the research view', () => {
    render(<EcosystemLanding featuredQuestions={[]} disclaimer={null} />)

    expect(
      screen.getByRole('heading', { name: 'From market question to grounded decision.' })
    ).toBeInTheDocument()
    expect(screen.getAllByTestId('ecosystem-node')).toHaveLength(6)
    expect(screen.getByRole('link', { name: /Enter market analysis/ })).toHaveAttribute(
      'href',
      '/research'
    )
    expect(screen.queryByRole('heading', { name: 'Featured questions' })).not.toBeInTheDocument()
  })

  test('marks each technology with its logo or the NVIDIA mark', () => {
    const { container } = render(<EcosystemLanding featuredQuestions={[]} disclaimer={null} />)
    const brands = (selector: string) =>
      [...container.querySelectorAll(selector)].map((mark) => mark.getAttribute('data-brand'))

    expect(new Set(brands('[data-brand]:has(img)'))).toEqual(
      new Set([
        'Nous Research',
        'DuckDB',
        'LangChain',
        'Milvus',
        'NVIDIA NIM',
        'RAPIDS',
        'OpenTelemetry',
        'Phoenix',
        'React',
        'Next.js',
        'FastAPI',
      ])
    )
    for (const image of container.querySelectorAll('[data-brand] img')) {
      expect(image.getAttribute('src')).toMatch(/^\/ecosystem-logos\/[a-z]+\.(svg|png)$/)
      expect(image).toHaveAttribute('alt', '')
    }
    // Every mark without a logo file is the NVIDIA mark; no text badges remain.
    expect(new Set(brands('[data-brand]:not(:has(img))'))).toEqual(new Set(['NVIDIA']))
    expect(screen.queryByText('K')).not.toBeInTheDocument()
    const mark = (name: string) => screen.getByText(name).parentElement!.firstElementChild!
    expect(mark('NVIDIA Kumo')).toHaveAttribute('data-brand', 'NVIDIA')
    expect(mark('NVIDIA Kumo').outerHTML).toBe(mark('Auto Ontology').outerHTML)
    expect(container.querySelector('[data-brand="LangChain"] img')).toHaveAttribute(
      'src',
      '/ecosystem-logos/langchain.svg'
    )
    expect(screen.queryByText('LC')).not.toBeInTheDocument()
    for (const library of ['cuDF', 'cuGraph', 'cuML']) {
      const chip = screen.getByText(library).parentElement!
      expect(chip.querySelector('[data-brand="RAPIDS"] img')).toHaveAttribute(
        'src',
        '/ecosystem-logos/rapids.svg'
      )
    }
    for (const name of ['OpenShell', 'Switchyard', 'LangChain', 'DuckDB', 'FastAPI']) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.queryByText('LlamaIndex')).not.toBeInTheDocument()
  })

  test('links each featured question to the research view', () => {
    render(
      <EcosystemLanding
        featuredQuestions={[QUESTION]}
        disclaimer="Synthetic market data. Not investment advice."
      />
    )

    expect(screen.getByText('Synthetic market data. Not investment advice.')).toBeInTheDocument()
    const link = screen.getByRole('link', { name: /Market Leaders/ })
    expect(link).toHaveAttribute('href', '/research?question=market-leaders')
    // The card may clamp the question; its full text stays in the link and its tooltip.
    expect(link).toHaveTextContent(QUESTION.question)
    expect(link).toHaveAttribute('title', QUESTION.question)
  })
})
