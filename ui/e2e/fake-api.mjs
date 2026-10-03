// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * A stand-in for the demo API, just enough for the live-mode smoke test:
 * the pack and its examples, its data sources, a job whose stream answers
 * immediately, and a transcription of any WAV recording.
 * Usage: FAKE_API_PORT=3990 node e2e/fake-api.mjs
 */

import { createServer } from 'node:http'

const PACK = {
  id: 'e2e',
  version: '1.0.0',
  title: 'E2E pack',
  disclaimer: 'Synthetic data for tests.',
  questions: [
    {
      id: 'market-leaders',
      label: 'Market Leaders',
      question: 'Which assets led the market?',
      sources: ['market_data'],
      tools: ['cudf'],
      featured: true,
    },
    // The default pack's other five featured questions, so the landing page is laid out with a
    // full set of six at their real length (see the no-scroll check in smoke.spec.ts).
    {
      id: 'news-sentiment-reaction',
      label: 'News & Price Reaction',
      question:
        "For company news about the 12 most liquid issuers published from August 17 through August 24, 2026, how did the sentiment labels line up with the issuers' returns over the following five sessions? Describe the relationship without claiming causation.",
      sources: ['market_data'],
      tools: ['cudf'],
      featured: true,
    },
    {
      id: 'unusual-sessions',
      label: 'Unusual Sessions',
      question:
        'Treat January 2 through June 30, 2026 as the baseline for every issuer. Which 10 issuer sessions from July 1 through August 31, 2026 were the most unusual in return, volatility and volume, and which features made each one unusual? Anomaly scores are neither forecasts nor explanations.',
      sources: ['market_data'],
      tools: ['cudf', 'cuml'],
      featured: true,
    },
    {
      id: 'peer-network',
      label: 'Peer Network',
      question:
        'In the return-correlation network from June through August 2026, which issuers are the most central, and which pairs moved together most closely?',
      sources: ['market_data'],
      tools: ['cudf', 'cugraph'],
      featured: true,
    },
    {
      id: 'cyber-disclosure-rules',
      label: 'Cybersecurity Disclosures',
      question:
        'What does Form 8-K Item 1.05 require a company to disclose about a material cybersecurity incident, and by when? Cite the regulation, and any second-quarter 2026 filings in the corpus that report an incident.',
      sources: ['sec_filings', 'market_regulations'],
      tools: ['retrieval'],
      featured: true,
    },
    {
      id: 'news-and-filings',
      label: 'News & Filings',
      question:
        'Which of the 12 most liquid issuers had the most negative company news in July and August 2026, and how did their prices react? Separately, which real second-quarter 2026 SEC filings describe operational disruptions? The issuers are fictional and are not the filers: keep the two apart.',
      sources: ['market_data', 'sec_filings'],
      tools: ['cudf', 'retrieval'],
      featured: true,
    },
    // Three more, so the composer's example picker has more than its five rows to show
    {
      id: 'outcome-prediction',
      label: 'Five-Session Outlook',
      question:
        'Which of the most liquid issuers is Kumo most confident will rise over the next five sessions?',
      sources: ['market_data'],
      tools: ['kumo'],
      featured: false,
    },
    {
      id: 'sector-sql',
      label: 'Sector Breakdown',
      question:
        'How many issuers does each sector have, and what was its median daily return in August 2026?',
      sources: ['market_data'],
      tools: ['ontology'],
      featured: false,
    },
    {
      id: 'volatility-ranking',
      label: 'Volatility Ranking',
      question: 'Which issuers were the most volatile in August 2026?',
      sources: ['market_data'],
      tools: ['cudf'],
      featured: false,
    },
  ],
  // The picker's questions, in its order: not volatility-ranking, and cyber-disclosure-rules needs a
  // source this API does not offer
  examples: [
    'unusual-sessions',
    'outcome-prediction',
    'peer-network',
    'sector-sql',
    'cyber-disclosure-rules',
    'market-leaders',
    'news-and-filings',
    'news-sentiment-reaction',
  ],
}

const DATA_SOURCES = [
  { id: 'market_data', name: 'Market data', description: 'Prices and volumes', kind: 'structured' },
  {
    id: 'sec_filings',
    name: 'SEC filings',
    description: 'Current reports',
    default_enabled: false,
    kind: 'documents',
  },
]

const ANSWER =
  'Asset A led the market [1].\n\n**References:**\n' +
  '- [1] Market analytics result — market scan — evidence `ev-1` — invocation `call-1`'

const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`

const json = (res, body) => {
  res.writeHead(200, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}

/** What the fake transcribes every recording to. */
const TRANSCRIPT = 'Which assets led the market?'

const readJson = async (req) => {
  let body = ''
  for await (const chunk of req) body += chunk
  return JSON.parse(body)
}

createServer(async (req, res) => {
  const { pathname } = new URL(req.url, 'http://fake-api')
  if (req.method === 'GET' && pathname === '/v1/pack') return json(res, PACK)
  if (req.method === 'GET' && pathname === '/v1/data_sources') return json(res, DATA_SOURCES)
  if (req.method === 'POST' && pathname === '/v1/jobs/async/submit') {
    const { job_id: jobId } = await readJson(req)
    return json(res, { job_id: jobId, status: 'submitted' })
  }
  if (req.method === 'POST' && pathname === '/v1/speech/transcriptions') {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const audio = Buffer.concat(chunks)
    const wav = audio.length > 44 && audio.subarray(0, 4).toString() === 'RIFF'
    if (!wav || req.headers['content-type'] !== 'audio/wav') return res.writeHead(422).end()
    return json(res, { text: TRANSCRIPT })
  }
  if (req.method === 'GET' && pathname.endsWith('/stream')) {
    res.writeHead(200, { 'content-type': 'text/event-stream' })
    res.write(sse('job.status', { status: 'running' }))
    res.write(
      sse('artifact.update', {
        data: { type: 'output', output_category: 'final_report', content: ANSWER },
      })
    )
    return res.end(sse('job.status', { status: 'success' }))
  }
  res.writeHead(404).end()
}).listen(Number(process.env.FAKE_API_PORT ?? 3990), '127.0.0.1')
