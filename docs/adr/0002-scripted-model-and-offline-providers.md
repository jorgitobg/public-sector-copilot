# ADR 0002: Scripted model and offline providers in the demo

Status: accepted (2026-11-06)

A demo that needs an API key or network cannot be reproduced by a reviewer in one command, and its output changes between runs. The model that chooses tool calls is therefore scripted, and the gateway's providers are offline stand-ins. The README says so in a dedicated section and the stand-in is named for what it does (`LeadLinesProvider`).

What this buys: byte-identical output, a CI check that the README equals reality, and tests of the seams (failover, denial, abstention) that would be flaky against a live model. What it costs: the demo cannot show model behaviour, and we do not claim it does. The components have been exercised against a live model individually; a live entry point now exists next to the offline demo, and its findings (including the abstention caused by lexical retrieval) are in the README rather than hidden.
