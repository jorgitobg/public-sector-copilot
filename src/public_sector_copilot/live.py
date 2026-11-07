"""Run the copilot with a real OpenAI-compatible model choosing the tools.

    COPILOT_API_KEY=... python -m public_sector_copilot.live --base-url URL --model NAME

The key is read from the environment only. The same tools, permissions and components as the
offline demo; only the model is real. No operator is attached, so the write tool is refused:
this run can read and answer, not save.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from agent_harness.adapters.confirmation import ScriptedConfirmation
from agent_harness.adapters.openai_compat import HttpPost, OpenAICompatConfig, OpenAICompatModel
from agent_harness.application.agent_loop import AgentLoop
from agent_harness.application.guardrails import GuardedRegistry
from agent_harness.domain.loop import AgentRun, Budget
from agent_harness.domain.policy import PermissionPolicy

from public_sector_copilot.copilot import Copilot

SYSTEM = (
    "You are a public-sector analyst. Use the tools: find_tenders for screened tenders, "
    "ask_boe for BOE questions (answers carry citations or abstain), summarize for long text. "
    "Do not invent facts that tools did not return. Be brief."
)
LIVE_TASK = (
    "Find the tender screened as GO and give its id and buyer. Then ask the BOE how many "
    "Subalterno posts the Ayuntamiento de Torrox offers. Report both, with the BOE source URL."
)


def run_live(
    config: OpenAICompatConfig, copilot: Copilot, task: str, http: HttpPost | None = None
) -> AgentRun:
    registry = copilot.registry()
    guarded = GuardedRegistry(registry, PermissionPolicy(), ScriptedConfirmation([]))
    loop = AgentLoop(
        OpenAICompatModel(config, http), registry, Budget(max_steps=8, timeout_s=180.0),
        dispatcher=guarded,
    )  # fmt: skip
    return loop.run(task)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="public-sector-copilot-live", description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--task", default=LIVE_TASK)
    parser.add_argument("--max-tokens", type=int, default=4096)
    args = parser.parse_args(argv)
    config = OpenAICompatConfig(
        args.base_url, args.model, os.environ.get("COPILOT_API_KEY") or None,
        max_tokens=args.max_tokens, system_prompt=SYSTEM,
    )  # fmt: skip
    run = run_live(config, Copilot(), args.task)
    for message in run.transcript:
        for call in message.tool_calls:
            print(f"agent -> {call.name}({dict(call.arguments)})")
    print(f"stop_reason={run.stop_reason.value} steps={run.steps} tokens={run.usage.total_tokens}")
    if run.final_answer:
        print(run.final_answer)
    return 0 if run.final_answer is not None else 1


if __name__ == "__main__":
    sys.exit(main())
