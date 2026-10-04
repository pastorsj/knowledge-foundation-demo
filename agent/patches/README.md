# Hermes patches

`agent/Dockerfile` applies these patches to `/opt/hermes` in the official
`nousresearch/hermes-agent:v2026.9.24` image (Hermes 0.21.5, upstream commit
`f97608f1`) with `git apply --exclude='tests/*'`. The image ships no `tests/`
tree, so the upstream test hunks are kept for upstreaming and skipped at build.
0001 to 0003 and 0005 touch the Runs API that the job API drives; 0004 and 0006, MCP discovery and calls.

| Patch | Why the demo needs it | Upstream | Remove when |
|---|---|---|---|
| `0001-runs-forward-relay-correlation-metadata` | Forwards the run request's `metadata` (job and session references) into the Relay turn, so Phoenix traces join to jobs. | [PR #107708](https://github.com/NousResearch/hermes-agent/pull/107708) covers the other API routes, not `/v1/runs`. | A released Runs API forwards request metadata into the agent turn. |
| `0002-runs-enforce-exact-per-run-toolsets` | Accepts `enabled_toolsets` on `/v1/runs` so each job gets only the tools its selected sources allow, and rejects any widening beyond the profile. | [PR #67837](https://github.com/NousResearch/hermes-agent/pull/67837), closed unmerged. | A released Runs API accepts an explicit, non-widening per-run toolset list. |
| `0003-runs-expose-stable-tool-call-identity` | Adds `tool_call_id` to `tool.started` and `tool.completed` Runs events, which joins graph nodes to receipts. | [PR #53642](https://github.com/NousResearch/hermes-agent/pull/53642), open. | A released Runs API emits the same `tool_call_id` on both events. |
| `0004-mcp-read-readonlyhint-from-mcp-2-annotations` | Hermes read `readOnlyHint` from mcp 2.x tool annotations, which name it `read_only_hint`, so every data tool counted as write-capable. A call cut off when an MCP server restarted (a rebuilt image, or Compose's restart policy after a crash) then failed instead of being replayed. | Draft ready (not filed). | Hermes reads `read_only_hint` from mcp 2.x annotations. |
| `0005-runs-export-each-run-as-one-relay-trace` | Relay exports a scope when it closes, and nothing closed an API-server session, so a job's spans hung from a root span that never reached Phoenix: no root in the trace list, no input or output in the session view. Each run now opens its session scope (the trace's `AGENT` root) with the run's metadata and question, and closes it with the answer. | Draft ready (not filed). | A released Runs API exports each run's Relay session. |
| `0006-mcp-propagate-the-relay-tool-span-to-mcp-servers` | An MCP `tools/call` carried no trace context, so each tool server's spans were a trace of their own. The tool callback now runs with Relay's tool span as the current OpenTelemetry span, and the MCP handler sends it in the request's `_meta` (`traceparent`, SEP-414), which MCP SDK servers read. | Draft ready (not filed). | Hermes propagates trace context on MCP calls. |

The patches apply in order: 0003 edits lines that 0002 adds. When bumping the
Hermes image, rebuild the agent image first; the `git apply` step fails the
build if any patch no longer applies.
