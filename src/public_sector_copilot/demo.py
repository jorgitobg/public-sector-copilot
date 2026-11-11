"""Offline demo: ``python -m public_sector_copilot.demo [--deny] [--primary-down]``."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

from public_sector_copilot.bridges import Gateway
from public_sector_copilot.copilot import Copilot, run_copilot


def _short(value: object, limit: int = 230) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    approve = "--deny" not in args
    copilot = Copilot(gateway=Gateway(primary_down="--primary-down" in args))
    result, confirmation = run_copilot(copilot, approve=approve)
    print(f"operator answer to confirmations: {'yes' if approve else 'no'}\n")
    for message in result.transcript:
        if message.tool_calls:
            for call in message.tool_calls:
                print(f"agent -> {call.name}({_short(dict(call.arguments), 110)})")
        if message.tool_result is not None:
            r = message.tool_result
            body = _short(r.output) if r.ok else f"REFUSED ({r.error_kind}) {r.error_message}"
            print(f"   <- {body}\n")
    print(f"confirmations asked: {confirmation.asked}")
    print(f"stop_reason={result.stop_reason.value} steps={result.steps}")
    print(f"final_answer={result.final_answer!r}")
    print(f"reports saved: {sorted(copilot.reports)}")
    print(f"gateway provider calls: { {p.name: p.calls for p in copilot.gateway.providers} }")
    save_failed = any(
        message.tool_result is not None
        and message.tool_result.name == "save_report"
        and not message.tool_result.ok
        for message in result.transcript
    )
    return 1 if save_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
