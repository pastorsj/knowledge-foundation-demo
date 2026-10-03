# Visual baselines

Screenshot baselines of the views that keep the original demo UI's look
(`parity.spec.ts`, the Playwright project `visual`):

| Baseline                         | View                                                            |
| -------------------------------- | --------------------------------------------------------------- |
| `landing-dark`, `landing-light`  | The landing page                                                |
| `research-data-sources`          | A new research session with the Data Sources panel and its hint |
| `question-picker`                | The demo scenario picker: five rows with their tool pills       |
| `recorded-list`                  | The Recorded list with its tool pills                           |
| `recorded-answer`                | A recorded answer with its Sources and Inspect actions          |
| `execution-graph`                | The execution workspace: replay bar, run summary and graph      |
| `explorer-market`                | The market tool explorer (anomaly scan)                         |
| `explorer-retrieval`             | The retrieval explorer                                          |
| `explorer-auto-ontology-sql`     | The Auto Ontology text-to-SQL explorer                          |
| `explorer-kumo`                  | The Kumo explorer                                               |
| `activity-thinking`, `-timeline` | The Agent Activity panel: Thinking and Timeline                 |
| `activity-benchmark`, `-milvus`  | The Benchmark tab, and its Milvus comparison                    |
| `data-viewer-table`, `-sql`      | The data viewer: a table, and a SQL query's result              |

The data never changes under them: the fixture pack (`e2e/fixtures/packs/e2e`, synthetic) and the
fake API (`e2e/fake-api.mjs`), never a pack's recordings, which are re-recorded. The page clock and
`Math.random` are fixed, toHaveScreenshot finishes CSS animations, the pointer rests on the app bar,
and `screenshot.css` hides the empty chat's starfield, a canvas that turns every frame. The views
still load the web fonts and icons from NVIDIA's CDN, so the run needs internet access.

## Where they render

In the official Playwright Docker image of the version in `package-lock.json`
(`mcr.microsoft.com/playwright:v<version>-noble`), at 1440x900 in the dark theme, with
`fonts.conf`: in that image the system font the UI asks for resolves to a font without bold, so
FreeSans and Liberation Mono (also in the image) stand in. The same image also runs on x86-64. The
project runs only there (`E2E_VISUAL=1`): screenshots from macOS or a desktop Linux differ in
fonts, so `npm run e2e` skips it.

## Check

```bash
npm run e2e:visual        # e2e/visual/run.sh: npm ci, build and compare, in the image
```

It needs Docker and Node. node_modules and the build live in container volumes, so a macOS
checkout's own are neither used nor changed. A failure leaves the expected, actual and diff images
of each view under `test-results/visual/`.

## Update

After a deliberate visual change:

```bash
npm run e2e:visual:update               # every view that changed
e2e/visual/run.sh --update -g 'Kumo'    # only the tests whose title matches
```

Then look at every changed PNG under `e2e/visual/__screenshots__/` before committing it: a baseline
is only as good as the review of its pixels. A new view needs a test in `parity.spec.ts` and a row
above. Bumping `@playwright/test` changes the image and its Chromium, so expect to update all of
them.

On an Apple silicon Mac the image runs natively as arm64 (its Chromium crashes under amd64
emulation). The baselines here were rendered that way, and an x86-64 run matched them with no
tolerance. If a future image renders the two differently, set a small `maxDiffPixelRatio` in the
`visual` project rather than rendering on one of them only.
