"""The agent: tools that front the four components, a scripted model, and the run loop.

The model is scripted so the demo is deterministic and keyless. Everything else is real:
tool calls go through the harness registry, permission policy and operator confirmation, and
reach the MCP server process, the retrieval pipeline and the gateway.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_harness.adapters.confirmation import ScriptedConfirmation
from agent_harness.adapters.scripted import ScriptedModel
from agent_harness.application.agent_loop import AgentLoop
from agent_harness.application.guardrails import GuardedRegistry
from agent_harness.application.registry import ToolRegistry
from agent_harness.domain.loop import AgentRun, Budget, ModelTurn, Usage
from agent_harness.domain.policy import PermissionPolicy
from agent_harness.domain.tools import PermissionLevel, ToolCall, ToolSpec

from public_sector_copilot.bridges import BoeAnswers, Gateway, McpToolbox

TASK = "Which open tender is worth pursuing, and what do the BOE notices say about staffing?"


def _schema(**props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


@dataclass
class Copilot:
    """Owns the component clients and the in-memory report store."""

    toolbox: McpToolbox = field(default_factory=McpToolbox)
    boe: BoeAnswers = field(default_factory=BoeAnswers)
    gateway: Gateway = field(default_factory=Gateway)
    reports: dict[str, str] = field(default_factory=lambda: {})
    _summaries: int = 0

    def find_tenders(self, args: Mapping[str, Any]) -> Any:
        data = self.toolbox.call("triage_tenders", {"verdict": str(args["verdict"]), "limit": 3})
        return {
            "summary": data["summary"],
            "tenders": [
                {k: t[k] for k in ("id", "source", "verdict", "buyer", "title")}
                for t in data["tenders"]
            ],
        }

    def summarize(self, args: Mapping[str, Any]) -> Any:
        self._summaries += 1
        result = self.gateway.summarize(str(args["text"]), f"copilot-{self._summaries}")
        return {"text": result["text"], "served_by": result["provider"],
                "attempts": [a["outcome"] for a in result["attempts"]]}  # fmt: skip

    def ask_boe(self, args: Mapping[str, Any]) -> Any:
        return self.boe.ask(str(args["question"]))

    def save_report(self, args: Mapping[str, Any]) -> Any:
        self.reports[str(args["title"])] = str(args["body"])
        return f"saved report {args['title']!r}"

    def registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register(
            ToolSpec("find_tenders", "List recorded tenders with a GO, REVIEW or NO_GO verdict.",
                     _schema(verdict={"type": "string", "enum": ["GO", "REVIEW", "NO_GO"]}),
                     PermissionLevel.READ),
            self.find_tenders,
        )  # fmt: skip
        reg.register(
            ToolSpec("summarize", "Summarize a text through the LLM gateway.",
                     _schema(text={"type": "string", "maxLength": 2000}), PermissionLevel.READ),
            self.summarize,
        )  # fmt: skip
        reg.register(
            ToolSpec("ask_boe", "BOE answers with citations, or abstains. Ask in Spanish.",
                     _schema(question={"type": "string", "maxLength": 300}), PermissionLevel.READ),
            self.ask_boe,
        )  # fmt: skip
        reg.register(
            ToolSpec("save_report", "Save the final report (needs operator approval).",
                     _schema(title={"type": "string"}, body={"type": "string", "maxLength": 4000}),
                     PermissionLevel.WRITE),
            self.save_report,
        )  # fmt: skip
        return reg


def scripted_turns() -> list[ModelTurn]:
    u = Usage(input_tokens=200, output_tokens=40, cost_usd=0.0)

    def step(say: str, call_id: str, name: str, /, **arguments: Any) -> ModelTurn:
        return ModelTurn(text=say, tool_calls=(ToolCall(call_id, name, arguments),), usage=u)

    return [
        step("Start with tenders already screened as GO.", "t1", "find_tenders", verdict="GO"),
        step("Summarize the buyer's notice for the report.", "t2", "summarize",
             text="Servicios de operación del Centro de Operaciones de Seguridad (SOC).\n"
                  "Plazo: 7 días. Valor estimado dentro de los límites del perfil.\n"
                  "Se valora experiencia previa en servicios gestionados de ciberseguridad.\n"
                  "Criterios de solvencia técnica según pliego."),
        step("Now check staffing notices in the BOE.", "t3", "ask_boe",
             question="¿Cuántas plazas de Subalterno convoca el Ayuntamiento de Torrox?"),
        step("One more question outside the corpus.", "t4", "ask_boe",
             question="¿Cuál es la capital de Francia?"),
        step("Save the report; this needs approval.", "t5", "save_report",
             title="tender-brief", body="GO: SOC tender. Torrox plazas (cited)."),
        ModelTurn(text="Finished: one GO tender, one cited BOE answer, one abstention.", usage=u),
    ]  # fmt: skip


def run_copilot(copilot: Copilot, *, approve: bool) -> tuple[AgentRun, ScriptedConfirmation]:
    registry = copilot.registry()
    confirmation = ScriptedConfirmation([approve])
    guarded = GuardedRegistry(registry, PermissionPolicy(), confirmation)
    loop = AgentLoop(
        ScriptedModel(scripted_turns()), registry, Budget(max_steps=8), dispatcher=guarded
    )
    return loop.run(TASK), confirmation
