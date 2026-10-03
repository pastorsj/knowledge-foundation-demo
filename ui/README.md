# UI

The demo's web app: a Next.js 16 / React 18 UI built on the upstream
[AI-Q UI](https://github.com/NVIDIA-AI-Blueprints/aiq/tree/develop/frontends/ui)
and the NVIDIA KUI component library. [UPSTREAM.md](UPSTREAM.md) records the base
commit and every change from it.

It has three pages, each on the selected pack (an industry, or "Your data"):

- `/`: the landing page, on the selected pack: an industry (retail, manufacturing, …) or "Your
  data", the workspace of the user's uploads. The industry selector sits in its header and in the
  app bar; the choice is the URL's `?pack=` and the `kf-pack` cookie (else `DEFAULT_PACK`). In live
  mode it lists the pack's featured questions (`GET /v1/pack?id=`); each opens `/research` with
  the question in the composer.
- `/research`: the chat. Every question is a durable job on the API, followed
  over Server-Sent Events until its answer arrives. Cited evidence opens in the
  execution view.
- `/api/*`: the server routes below.

## How it fits

```
browser ──► ui (this) ──/api/v1/*──► api:8000 ──► Hermes (OpenShell sandbox)
                │
                └──/api/recordings/<pack>/*──► /packs/<pack>/recordings (read-only)
```

The browser only talks to this origin. The server routes are:

| Route                        | Purpose                                                                                                                                                                                                                                                                                                                                                                   |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /api/health`            | UI liveness, for the container healthcheck. Never calls the API.                                                                                                                                                                                                                                                                                                          |
| `GET, POST /api/v1/<path>`   | Proxy to `$API_URL/v1/<path>`, limited to `pack`, `data_sources/**` (and `POST data_sources/{id}/query`), `POST jobs/async/submit`, `GET jobs/async/job/{id}/**`, `POST jobs/async/job/{id}/cancel` and `…/benchmark`, and `POST speech/transcriptions` (a WAV of at most 3 MiB). Anything else, including `/internal/**`, is a 404. SSE streams pass through unbuffered. |
| `GET /api/recordings/<pack>/<path>` | Files of a pack's replay bundle (`.json`, `.jsonl` only); `/api/recordings/packs.json` lists the packs with a bundle. |

## Modes

`UI_MODE=live` (default) submits questions to the API. `UI_MODE=replay` shows
only the recorded sessions of the data pack, under "Recorded" as the original
demo UI did: the composer and the data source selection are read only, the data
sources come from the bundle's `pack.json`, and the `/api/v1` proxy answers 404
without calling the API.

## The execution view

The execution view (graph, capability explorers, timeline, replay) lives in
`src/features/execution` and plugs in through one typed interface,
`ExecutionFeature` in `src/shared/context/ExecutionFeatureContext.tsx`:

| Slot                | Used for                                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `onJobEvent(event)` | Every SSE record of a live job, in order (the event names in `JOB_STREAM_EVENTS`, `adapters/api/deep-research-client.ts`) |
| `Workspace`         | The view opened by "View Execution" or a cited evidence source                                                            |
| `ActivityPanel`     | The "Agent Activity" side panel's tabs: Thinking, Timeline and Benchmark                                                  |
| `recordings`        | `list()` and `load(id)` of recorded sessions in replay mode                                                               |

`src/app/providers.tsx` wires the implementation. Until it exists, the UI runs
with `noExecutionFeature` and simply hides those parts.

The execution graph is a fixed topology drawn with plain HTML and SVG, with its
own pan and zoom. The explorers and the data viewer are drawn the same way, with
no graph library.

## Configuration

Runtime environment, read per request (see [.env.example](.env.example)):

| Variable                                           | Default            | Meaning                                                                                           |
| -------------------------------------------------- | ------------------ | ------------------------------------------------------------------------------------------------- |
| `UI_MODE`                                          | `live`             | `live` or `replay`                                                                                |
| `API_URL`                                          | `http://api:8000`  | Demo API, server-side only                                                                        |
| `PACKS_DIR`                                        | `/packs`           | Directory of data packs                                                                           |
| `DEFAULT_PACK`                                     | `retail`           | The pack shown until the user picks another; recordings come from `$PACKS_DIR/<pack>/recordings` |
| `PHOENIX_URL`                                      | unset              | Browser-reachable Phoenix UI; unset hides the Phoenix link                                        |
| `FILE_UPLOAD_ACCEPTED_TYPES`                       | documents and tables the ingest service reads | Your data: the extensions the composer and the Files tab accept |
| `FILE_UPLOAD_MAX_SIZE_MB`, `FILE_UPLOAD_MAX_FILE_COUNT` | `100`, `20`   | Your data: the largest file, and the most files per upload |
| `FILE_UPLOAD_MAX_REQUEST_MB`                       | `512`              | The largest upload request the proxy forwards (413 past it) |
| `SPEECH_INPUT_ENABLED`, `SPEECH_INPUT_MAX_SECONDS` | `false`, `60`      | The composer's microphone (live mode only; the API transcribes), and the longest recording        |
| `PORT`, `HOSTNAME`                                 | `3000`, `0.0.0.0`  | Listen address of the container's server (`npm start` and `npm run dev` listen on 127.0.0.1 only) |

Icons load from NVIDIA's brand-asset CDN, so the browser needs internet access.

## Run

```bash
npm ci
cp .env.example .env.local    # then adjust
npm run dev                   # http://127.0.0.1:3000
```

Production build, as in the container:

```bash
npm run build                 # .next/standalone, with static assets copied in
npm start                     # http://127.0.0.1:3000 (set PORT to change the port)
docker build -t knowledge-foundation/ui:local .
```

In the full stack, `compose.yaml` runs this image as the `ui` service.

## Test

```bash
npm run lint
npm run type-check
npm run test:ci               # Vitest + coverage
npm run build && npm run e2e  # Playwright (Chromium): live smoke and every recorded session
npm run e2e:visual            # visual baselines, in the Playwright Docker image
```

The e2e tests start servers from the build: live mode against
`e2e/fake-api.mjs` (packs retail, manufacturing and the workspace; a documents API whose files move
one pipeline stage per status poll; jobs answering with cited reports), replay mode on the synthetic
fixture packs in `e2e/fixtures/packs` (retail and manufacturing), and replay mode on the industry
packs' committed recordings in `../data/packs` (a pack without a recordings bundle is skipped).
`e2e/smoke.spec.ts` switches industries (the picker's examples, the sources panel and `?pack=`
follow) and uploads `e2e/fixtures/files/{policy.pdf,orders.csv}` to Your data, watches them reach
Available, and asks a cited question about them. Every recorded session must replay without calling the API: its
Recorded list entry with the tool pills its runs used, and for each turn the question, the answer,
its cited sources and its run down to the closing events. The fake API offers retail's six featured questions
and three more; seven of them are the picker's examples, more than the five rows it shows.
It needs `npx playwright install chromium` once; on Linux, `npx playwright install --with-deps chromium`,
which also installs Chromium's system libraries with apt (sudo), as CI and `demo.sh test e2e` do.

`e2e-live/` is the live end-to-end test of a running deployment, `scripts/demo.sh test live --url URL`
([operations](../docs/operations.md#on-demand-checks)), which CI never runs. `playwright.live.config.ts` runs it
with no server of its own; its checks (`e2e-live/checks.ts`) are unit-tested with Vitest in
`e2e-live/checks.test.ts`. To try the whole test without a deployment, point a live-mode UI at
`e2e-live/fake-deployment.mjs`, a stand-in API that answers each question with its recorded session:

```bash
npm run build
node e2e-live/fake-deployment.mjs &   # 127.0.0.1:3997; DATA_PACK picks the pack (default synthetic-market)
HOSTNAME=127.0.0.1 PORT=3998 UI_MODE=live API_URL=http://127.0.0.1:3997 node .next/standalone/server.js &
../scripts/demo.sh test live --url http://127.0.0.1:3998
```

The visual baselines (`e2e/visual`) are screenshots of the views that keep the original demo UI's
look, on the fixture pack and the fake API only. They render in the official Playwright Docker
image; [e2e/visual/README.md](e2e/visual/README.md) says how to check and update them.
