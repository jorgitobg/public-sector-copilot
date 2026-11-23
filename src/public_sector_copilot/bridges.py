"""Adapters from the four components to agent-harness tools.

Each component is reached the way a real deployment would reach it:
- mcp-toolbox over MCP/stdio, as a separate server process spoken to by the official client;
- sovereign-rag as a library (retrieval plus mandatory-citation answers);
- llm-gateway over HTTP (in-process ASGI transport here, so no port is opened).
The agent loop is synchronous, so async calls run on a private event loop.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import re
import sys
import threading
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
    """Calls the real mcp-toolbox server process over one long-lived stdio session.

    The agent loop is synchronous, so the session lives on a private event loop in a helper
    thread. The server process starts lazily on the first call, is reused by every later call
    and is stopped by ``close()`` (or leaving the ``with`` block). A call after ``close()``
    starts a fresh session.
    """

    def __init__(
        self, params: StdioServerParameters | None = None, *, call_timeout_s: float = 60.0
    ) -> None:
        self._params = params or StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_toolbox"]
        )
        self.call_timeout_s = call_timeout_s
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._session: ClientSession | None = None

    def __enter__(self) -> McpToolbox:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        with self._lock:
            session, loop = self._ensure_started()
        future = asyncio.run_coroutine_threadsafe(session.call_tool(tool, arguments), loop)
        try:
            result = future.result(timeout=self.call_timeout_s)
        except concurrent.futures.TimeoutError as error:
            future.cancel()
            raise RuntimeError(f"mcp tool {tool} timed out") from error
        first = result.content[0] if result.content else None
        text = str(getattr(first, "text", ""))
        if result.isError:
            raise RuntimeError(f"mcp tool {tool} failed: {text[:200]}")
        return json.loads(text)

    def close(self) -> None:
        with self._lock:
            thread, loop, stop = self._thread, self._loop, self._stop
            self._thread = self._loop = self._stop = self._session = None
        if thread is None or loop is None or stop is None:
            return
        loop.call_soon_threadsafe(stop.set)
        thread.join(timeout=15)

    def _ensure_started(self) -> tuple[ClientSession, asyncio.AbstractEventLoop]:
        if self._session is not None and self._loop is not None:
            return self._session, self._loop
        loop = asyncio.new_event_loop()
        ready: concurrent.futures.Future[tuple[ClientSession, asyncio.Event]] = (
            concurrent.futures.Future()
        )
        thread = threading.Thread(
            target=self._run, args=(loop, ready), name="mcp-toolbox-session", daemon=True
        )
        thread.start()
        try:
            session, stop = ready.result(timeout=self.call_timeout_s)
        except BaseException:
            thread.join(timeout=15)
            raise
        self._thread, self._loop, self._stop, self._session = thread, loop, stop, session
        return session, loop

    def _run(
        self,
        loop: asyncio.AbstractEventLoop,
        ready: concurrent.futures.Future[tuple[ClientSession, asyncio.Event]],
    ) -> None:
        try:
            loop.run_until_complete(self._serve(ready))
        finally:
            loop.close()

    async def _serve(
        self, ready: concurrent.futures.Future[tuple[ClientSession, asyncio.Event]]
    ) -> None:
        # The transport and session contexts must be entered and left by this one task.
        stop = asyncio.Event()
        try:
            async with (
                stdio_client(self._params) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                ready.set_result((session, stop))
                await stop.wait()
        except BaseException as error:
            if not ready.done():
                ready.set_exception(error)
            elif not isinstance(error, Exception):
                raise


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
