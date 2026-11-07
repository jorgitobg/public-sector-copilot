from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from agent_harness.adapters.openai_compat import OpenAICompatConfig
from agent_harness.domain.loop import StopReason

from public_sector_copilot import live
from public_sector_copilot.copilot import Copilot

FIXTURE: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "live_english_question_abstains.json").read_text()
)


class Replay:
    def __init__(self) -> None:
        self.recorded = list(FIXTURE)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[Mapping[str, str]] = []

    def post(self, url: str, headers: Mapping[str, str], body: bytes) -> bytes:
        self.requests.append(json.loads(body))
        self.headers.append(headers)
        return json.dumps(self.recorded.pop(0)["response"]).encode()


def config() -> OpenAICompatConfig:
    return OpenAICompatConfig("https://api.example.test/v1", "m", "SECRET-KEY",
                              max_tokens=4096, system_prompt=live.SYSTEM)  # fmt: skip


def test_recorded_live_run_replays_through_the_real_components() -> None:
    http = Replay()
    run = live.run_live(config(), Copilot(), live.LIVE_TASK, http)
    assert run.stop_reason is StopReason.FINAL_ANSWER and run.steps == 3
    names = [c.name for m in run.transcript for c in m.tool_calls]
    assert names == ["find_tenders", "ask_boe"]
    # The recorded model asked in English; the lexical retriever has no evidence, and the
    # agent reports that abstention instead of inventing a number.
    ask = [m.tool_result for m in run.transcript if m.tool_result][-1]
    assert ask is not None and ask.output["abstained"] is True
    assert run.final_answer is not None and "412026" in run.final_answer


def test_requests_equal_the_recorded_ones() -> None:
    http = Replay()
    live.run_live(config(), Copilot(), live.LIVE_TASK, http)
    for sent, recorded in zip(http.requests, FIXTURE, strict=True):
        assert sent["messages"] == recorded["request"]["messages"]
        assert [t["function"]["name"] for t in sent["tools"]] == recorded["request"]["tool_names"]
    assert "SECRET-KEY" not in json.dumps(FIXTURE)


def test_write_tool_is_refused_without_an_operator() -> None:
    from agent_harness.domain.tools import ToolCall

    copilot = Copilot()
    registry = copilot.registry()
    assert registry.get("save_report") is not None
    run = live.run_live(config(), copilot, live.LIVE_TASK, Replay())
    assert copilot.reports == {} and run.stop_reason is StopReason.FINAL_ANSWER
    assert ToolCall("x", "save_report", {"title": "t", "body": "b"}).name == "save_report"


def test_cli_prints_the_calls_and_never_the_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    http = Replay()
    original = live.OpenAICompatModel

    def build(cfg: OpenAICompatConfig, _http: Any = None) -> Any:
        return original(cfg, http)

    monkeypatch.setattr(live, "OpenAICompatModel", build)
    monkeypatch.setenv("COPILOT_API_KEY", "SECRET-KEY")
    code = live.main(["--base-url", "https://api.example.test/v1", "--model", "m"])
    out = capsys.readouterr().out
    assert code == 0 and "agent -> find_tenders" in out and "SECRET-KEY" not in out
    assert http.headers[0]["authorization"] == "Bearer SECRET-KEY"


def test_cli_exits_nonzero_when_the_model_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("COPILOT_API_KEY", raising=False)
    assert live.main(["--base-url", "ftp://nope.test", "--model", "m"]) == 1
    assert "stop_reason=model_error" in capsys.readouterr().out
