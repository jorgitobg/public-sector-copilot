# public-sector-copilot

An integration demo: one agent that triages public tenders and answers questions about BOE notices **with citations or an honest "no evidence"**. It is built by composing four independent open-source components, each reached the way a real deployment would reach it.

```
                       ┌──────────────────────────────────────────┐
 task ──► agent loop ──┤ agent-harness: tool registry, schemas,   │
          (scripted    │ permission policy, operator confirmation │
           model)      └───┬───────────────┬───────────────┬──────┘
                           │               │               │
                  find_tenders         ask_boe         summarize
                           │               │               │
                   mcp-toolbox      sovereign-rag     llm-gateway
                  (MCP over stdio,  (retrieval + cited (HTTP: limits,
                   separate process) answers/abstains)  failover, breaker)
```

| Component | How it is used here | Boundary |
|---|---|---|
| [agent-harness](https://github.com/jorgitobg/agent-harness) | runs the loop, validates arguments, enforces permissions | library |
| [mcp-toolbox](https://github.com/jorgitobg/mcp-toolbox) | tender triage over the recorded open-tender-radar report | **MCP server process over stdio** |
| [sovereign-rag](https://github.com/jorgitobg/sovereign-rag) | cited answers over bundled BOE notices | library |
| [llm-gateway](https://github.com/jorgitobg/llm-gateway) | summarization with versioned prompt, failover, circuit breaker | **HTTP (in-process ASGI)** |

The point of the repo is the seams: versions are pinned by commit, each piece stays independently usable, and nothing here reaches into another component's internals.

## Run it (no keys, no network)

Python 3.12+.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
python -m public_sector_copilot.demo
```

Real output:

```text
operator answer to confirmations: yes

agent -> find_tenders({"verdict": "GO"})
   <- {"summary": {"matched": 10, "parsed": 11, "skipped": 0, "verdicts": {"GO": 1, "NO_GO": 7, "REVIEW": 2}}, "tenders": [{"buyer": "Presidencia y Dirección de Administración y Finanzas de la de la Sociedad Mercantil Estatal para la G…

agent -> summarize({"text": "Servicios de operación del Centro de Operaciones de Seguridad (SOC).\nPlazo: 7 días. Valor estimado…)
   <- {"attempts": ["ok"], "served_by": "primary", "text": "Servicios de operación del Centro de Operaciones de Seguridad (SOC).\nPlazo: 7 días. Valor estimado dentro de los límites del perfil.\nSe valora experiencia previa en servicio…

agent -> ask_boe({"question": "¿Cuántas plazas de Subalterno convoca el Ayuntamiento de Torrox?"})
   <- {"abstained": false, "answer": "Turno libre: Subalterno/a 8 plazas [1]", "reason": "", "sources": [{"title": "Resolución de 28 de octubre de 2026, del Ayuntamiento de Torrox (Málaga), referente a la convocatoria para proveer vari…

agent -> ask_boe({"question": "¿Cuál es la capital de Francia?"})
   <- {"abstained": true, "answer": "Sin evidencia en el corpus para responder a esta pregunta.", "reason": "no_evidence", "sources": []}

agent -> save_report({"body": "GO: SOC tender. Torrox plazas (cited).", "title": "tender-brief"})
   <- "saved report 'tender-brief'"

confirmations asked: [('save_report', 'write')]
stop_reason=final_answer steps=6
final_answer='Finished: one GO tender, one cited BOE answer, one abstention.'
reports saved: ['tender-brief']
gateway provider calls: {'primary': 1, 'backup': 0}
```

The write (`save_report`) is the only action that needs the operator. Denying it, with the primary provider down, shows both guardrails at once:

```bash
python -m public_sector_copilot.demo --deny --primary-down
```

```text
operator answer to confirmations: no

agent -> find_tenders({"verdict": "GO"})
   <- {"summary": {"matched": 10, "parsed": 11, "skipped": 0, "verdicts": {"GO": 1, "NO_GO": 7, "REVIEW": 2}}, "tenders": [{"buyer": "Presidencia y Dirección de Administración y Finanzas de la de la Sociedad Mercantil Estatal para la G…

agent -> summarize({"text": "Servicios de operación del Centro de Operaciones de Seguridad (SOC).\nPlazo: 7 días. Valor estimado…)
   <- {"attempts": ["unavailable", "ok"], "served_by": "backup", "text": "Servicios de operación del Centro de Operaciones de Seguridad (SOC).\nPlazo: 7 días. Valor estimado dentro de los límites del perfil.\nSe valora experiencia prev…

agent -> ask_boe({"question": "¿Cuántas plazas de Subalterno convoca el Ayuntamiento de Torrox?"})
   <- {"abstained": false, "answer": "Turno libre: Subalterno/a 8 plazas [1]", "reason": "", "sources": [{"title": "Resolución de 28 de octubre de 2026, del Ayuntamiento de Torrox (Málaga), referente a la convocatoria para proveer vari…

agent -> ask_boe({"question": "¿Cuál es la capital de Francia?"})
   <- {"abstained": true, "answer": "Sin evidencia en el corpus para responder a esta pregunta.", "reason": "no_evidence", "sources": []}

agent -> save_report({"body": "GO: SOC tender. Torrox plazas (cited).", "title": "tender-brief"})
   <- REFUSED (denied) operator refused tool 'save_report'

confirmations asked: [('save_report', 'write')]
stop_reason=final_answer steps=6
final_answer='Finished: one GO tender, one cited BOE answer, one abstention.'
reports saved: []
gateway provider calls: {'primary': 1, 'backup': 1}
```

What to notice: the failed primary (`"unavailable"` then `"ok"`) is visible to the caller; the denied write leaves no report; the question outside the corpus abstains instead of inventing an answer.

## What is real and what is simulated

- **Real:** the MCP server process and protocol, the retrieval index and citation rules, the harness permission checks and argument validation, the gateway request limits, prompt template, failover and breaker.
- **Simulated, on purpose:** the model that decides which tool to call is **scripted**, and the gateway's providers are offline stand-ins that return the first lines of the text (they do not summarize). This keeps the demo deterministic and keyless. It means the demo shows the **architecture and its guardrails, not model quality**.
- The tender data is a recorded report over public TED/PLACSP notices screened against a **fictional** profile. The BOE notices are bundled public text. No private or client data is involved.

## With a real model choosing the tools

```bash
COPILOT_API_KEY=... python -m public_sector_copilot.live \
  --base-url https://openrouter.ai/api/v1 --model <model-name>
```

Same tools, permissions and components; only the model is real. No operator is attached, so `save_report` is refused and this run can read and answer, not save. The key comes from the environment only.

What was observed on 2026-11-06, one free-tier model (`nvidia/nemotron-3.5-lightning:free`) through OpenRouter, a handful of runs:

- It chose the tools on its own: `find_tenders` with the GO filter, then `ask_boe`, and reported the tender id and buyer correctly from the tool output.
- **The BOE question came back as an abstention both times, so the report has no Torrox answer.** The first run asked in English. After the tool description was changed to say "Ask in Spanish", the model asked in Spanish ("puestos de subalterno") and still got no evidence, because the bundled retriever is lexical and the notice says "plazas", not "puestos". The agent reported the abstention honestly instead of inventing a number. The scripted demo works because its question uses the corpus wording.
- A second free model (`google/gemma-4-31b-it:free`) failed with a typed `model_error` before any tool call; I did not inspect the cause.

So the live run shows the guardrails holding under a real model, and it also exposes a real weakness of lexical retrieval: synonyms. That is a finding, not a pass.

The first run is recorded in `tests/fixtures/live_english_question_abstains.json` (no headers or credentials) and the tests replay it through the real components, asserting the requests equal the recorded ones. The Spanish run was not recorded. One free model on one day is an anecdote, not a benchmark.

## Using other models

The components already support it individually: `agent-harness` has an OpenAI-compatible adapter (`python -m agent_harness.live`) and `llm-gateway` can chain a real OpenAI-compatible provider after the offline one. The live entry point above wires one in; the offline demo stays scripted.

## Development

```bash
pip install -e '.[dev]'
ruff check . && ruff format --check . && pyright && pytest
```

CI runs those plus a check that the output above equals the real demo output, and a secret scan over the full history. Coverage gate: 85 %.

## Limits

- Not a product and not for decisions: the verdicts come from a rule-based screen over a fictional profile.
- The offline demo's turns are scripted, so it will not adapt to a different task without changing the script. The live entry point does adapt, subject to the retrieval weakness above.
- Each MCP tool call starts a fresh server process. Fine for a demo, wasteful for production, where one long-lived session would be used.
- Two dependencies do not ship type information yet, so strict typing stops at their boundary.

## License

Apache-2.0.
