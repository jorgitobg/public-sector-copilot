"""Adapters from the four components to agent-harness tools.

Each component is reached the way a real deployment would reach it:
- mcp-toolbox over MCP/stdio, as a separate server process spoken to by the official client;
- sovereign-rag as a library (retrieval plus mandatory-citation answers);
- llm-gateway over HTTP (in-process ASGI transport here, so no port is opened).
The agent loop is synchronous, so each async call runs on its own short event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
from llm_gateway.app import create_app
from llm_gateway.errors import ProviderUnavailable
from llm_gateway.logging_setup import ACCESS_LOGGER
from llm_gateway.providers import CompletionRequest, Provider, ProviderChain
from llm_gateway.settings import Settings
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sovereign_rag.adapters.extractive import ExtractiveModel
from sovereign_rag.application.answer import answer_question
from sovereign_rag.wiring import build_retriever, default_corpus_dir

_TEXT = re.compile(r"<<<TEXT\n(.*?)\nTEXT>>>", re.DOTALL)


class McpToolbox:
    """Calls the real mcp-toolbox server process; one short session per call."""

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        return asyncio.run(self._call(tool, arguments))

    async def _call(self, tool: str, arguments: dict[str, Any]) -> Any:
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_toolbox"])
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
        # Raise after the transport context has closed: raising inside an anyio task group
        # would wrap the error in an ExceptionGroup and hide its type from the agent loop.
        first = result.content[0] if result.content else None
        text = str(getattr(first, "text", ""))
        if result.isError:
            raise RuntimeError(f"mcp tool {tool} failed: {text[:200]}")
        return json.loads(text)


class BoeAnswers:
    """Cited answers from sovereign-rag. Abstains when the corpus has no evidence."""

    def __init__(self) -> None:
        self._retriever, _ = build_retriever(default_corpus_dir())
        self._model = ExtractiveModel()

    def ask(self, question: str) -> dict[str, Any]:
        answer = answer_question(question, self._retriever, self._model)
        seen: dict[str, str] = {}
        for citation in answer.citations:
            seen.setdefault(citation.url, citation.title)
        return {
            "answer": answer.text,
            "abstained": answer.abstained,
            "reason": answer.reason,
            "sources": [{"title": title, "url": url} for url, title in seen.items()],
        }


class LeadLinesProvider:
    """Offline stand-in for a language model: returns the first lines of the text it is given.

    It is NOT a model and cannot paraphrase. It exists so the gateway path (limits, versioned
    prompt, failover, breaker) runs with no key and no network.
    """

    def __init__(self, name: str, *, down: bool = False, max_lines: int = 4) -> None:
        self.name = name
        self.down = down
        self.max_lines = max_lines
        self.calls = 0

    async def complete(self, request: CompletionRequest) -> str:
        self.calls += 1
        if self.down:
            raise ProviderUnavailable("simulated outage")
        match = _TEXT.search(request.prompt)
        body = match.group(1) if match else request.prompt
        return "\n".join(body.splitlines()[: self.max_lines])


@dataclass
class Gateway:
    """llm-gateway reached over HTTP. ``primary_down`` shows failover without a real outage."""

    primary_down: bool = False
    providers: list[LeadLinesProvider] = field(default_factory=lambda: [])

    def __post_init__(self) -> None:
        self.providers = [
            LeadLinesProvider("primary", down=self.primary_down),
            LeadLinesProvider("backup"),
        ]
        providers: list[Provider] = [*self.providers]
        chain = ProviderChain(providers, timeout_s=2.0, breaker_threshold=2)
        self._app = create_app(chain=chain, settings=Settings(max_prompt_chars=4000))
        logging.getLogger(ACCESS_LOGGER).setLevel(logging.CRITICAL)  # keep demo output readable

    def summarize(self, text: str, request_id: str) -> dict[str, Any]:
        return asyncio.run(self._summarize(text, request_id))

    async def _summarize(self, text: str, request_id: str) -> dict[str, Any]:
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            response = await client.post(
                "/v1/summarize", json={"text": text}, headers={"x-request-id": request_id}
            )
        if response.status_code != 200:
            raise RuntimeError(f"gateway returned {response.status_code}")
        return dict(response.json())


__all__ = ["BoeAnswers", "Gateway", "LeadLinesProvider", "McpToolbox"]
