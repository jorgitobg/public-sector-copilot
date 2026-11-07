# Spec 0001: public-sector-copilot

## Purpose
Show that four separately published components compose into one agent without coupling, and that the guardrails of each survive composition.

## User stories and acceptance
- **US1 tender triage over MCP.** Given the GO filter, when the agent calls `find_tenders`, then the call reaches the real `mcp-toolbox` server process and returns only compact fields. A tool error becomes a failed tool result, not a crash.
- **US2 cited answers or abstention.** Given a question the BOE corpus answers, the result has an answer and at least one source URL on `boe.es`. Given a question it does not, `abstained` is true and sources are empty.
- **US3 gateway with failover.** Given the primary provider is down, `summarize` is served by the backup and the caller sees `["unavailable", "ok"]`.
- **US4 permissions.** `save_report` is a write tool: it asks the operator. If denied, no report is stored and the denial is a typed refusal in the transcript.
- **US5 argument validation.** Invalid or oversized arguments are rejected by the harness before any component is called.
- **US6 determinism.** Two runs of the demo produce byte-identical output.

## Non-goals
Model quality, latency or throughput measurement, a long-lived MCP session, a UI.

## Done since the first version
- **US7 live model.** `public_sector_copilot.live` runs the same tools with a real OpenAI-compatible model; a recorded real run is replayed in tests.

## Follow-ups (not done)
1. Record a successful live run as a second fixture.
2. Publish type information from `llm-gateway` and `sovereign-rag`.
3. Reuse one MCP session across calls.
