"""One MCP stdio server process per toolbox session, closed on every exit path."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest
from agent_harness.domain.loop import StopReason
from mcp import StdioServerParameters

from public_sector_copilot import copilot as copilot_module
from public_sector_copilot.bridges import McpToolbox
from public_sector_copilot.copilot import Copilot, run_copilot

# Records the server's pid, then runs the real mcp-toolbox server unchanged.
_LAUNCHER = (
    "import os, runpy, sys;"
    "open(sys.argv[1], 'a').write(str(os.getpid()) + '\\n');"
    "sys.argv = ['mcp_toolbox'];"
    "runpy.run_module('mcp_toolbox', run_name='__main__')"
)


def recording(pid_file: Path) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-c", _LAUNCHER, str(pid_file)])


def pids(pid_file: Path) -> list[int]:
    return [int(line) for line in pid_file.read_text().split()] if pid_file.exists() else []


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.05)
    return not alive(pid)


def test_many_calls_share_one_server_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    with McpToolbox(recording(pid_file)) as toolbox:
        for verdict in ("GO", "REVIEW", "NO_GO", "GO"):
            toolbox.call("triage_tenders", {"verdict": verdict, "limit": 3})
        assert len(pids(pid_file)) == 1
        assert alive(pids(pid_file)[0])
    assert wait_gone(pids(pid_file)[0])


def test_independent_toolboxes_do_not_share_a_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    for _ in range(2):
        with McpToolbox(recording(pid_file)) as toolbox:
            toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})
    first, second = pids(pid_file)
    assert first != second
    assert wait_gone(first) and wait_gone(second)


def test_a_tool_error_does_not_end_the_session(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    with McpToolbox(recording(pid_file)) as toolbox:
        with pytest.raises(RuntimeError, match="get_boe_document"):
            toolbox.call("get_boe_document", {"doc_id": "does-not-exist"})
        assert toolbox.call("triage_tenders", {"verdict": "GO", "limit": 5})["count"] == 1
    assert len(pids(pid_file)) == 1


def test_exception_inside_the_with_block_closes_the_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    with pytest.raises(ValueError, match="boom"), McpToolbox(recording(pid_file)) as toolbox:
        toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})
        raise ValueError("boom")
    assert wait_gone(pids(pid_file)[0])


def test_close_is_idempotent_and_unused_toolbox_spawns_nothing(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    toolbox = McpToolbox(recording(pid_file))
    toolbox.close()
    toolbox.close()
    assert pids(pid_file) == []


def test_a_call_after_close_starts_a_fresh_session(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    toolbox = McpToolbox(recording(pid_file))
    toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})
    toolbox.close()
    toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})
    toolbox.close()
    first, second = pids(pid_file)
    assert first != second and wait_gone(first) and wait_gone(second)


def test_failed_startup_raises_and_leaves_nothing_running(tmp_path: Path) -> None:
    broken = StdioServerParameters(command=sys.executable, args=["-c", "raise SystemExit(3)"])
    toolbox = McpToolbox(broken)
    with pytest.raises(Exception):  # noqa: B017 - the SDK's error type is not public API
        toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})
    toolbox.close()


def test_a_run_uses_one_server_process_and_closes_it(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    copilot = Copilot(toolbox=McpToolbox(recording(pid_file)))
    result, _ = run_copilot(copilot, approve=True)
    assert result.stop_reason is StopReason.FINAL_ANSWER
    assert len(pids(pid_file)) == 1
    assert wait_gone(pids(pid_file)[0])


def test_a_denied_run_also_closes_the_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    copilot = Copilot(toolbox=McpToolbox(recording(pid_file)))
    run_copilot(copilot, approve=False)
    assert len(pids(pid_file)) == 1 and wait_gone(pids(pid_file)[0])


def test_an_exception_in_the_loop_closes_the_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pid_file = tmp_path / "pids"
    copilot = Copilot(toolbox=McpToolbox(recording(pid_file)))
    copilot.toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})

    class Exploding:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def run(self, task: str) -> object:
            raise KeyboardInterrupt

    monkeypatch.setattr(copilot_module, "AgentLoop", Exploding)
    with pytest.raises(KeyboardInterrupt):
        run_copilot(copilot, approve=True)
    assert wait_gone(pids(pid_file)[0])


def test_two_runs_of_one_copilot_do_not_share_a_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    copilot = Copilot(toolbox=McpToolbox(recording(pid_file)))
    run_copilot(copilot, approve=True)
    run_copilot(copilot, approve=True)
    first, second = pids(pid_file)
    assert first != second


def test_a_call_that_exceeds_its_timeout_is_reported(tmp_path: Path) -> None:
    with McpToolbox(recording(tmp_path / "pids")) as toolbox:
        toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})  # session is up
        toolbox.call_timeout_s = 0.0
        with pytest.raises(RuntimeError, match="timed out"):
            toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})


def test_startup_timeout_stops_the_process_and_the_thread(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    slow = StdioServerParameters(
        command=sys.executable,
        args=[
            "-c",
            "import os, sys, time;"
            "open(sys.argv[1], 'a').write(str(os.getpid()) + '\\n');"
            "time.sleep(60)",
            str(pid_file),
        ],
    )
    toolbox = McpToolbox(slow, call_timeout_s=1.0)
    before = set(threading.enumerate())
    with pytest.raises(Exception):  # noqa: B017 - timeout type comes from concurrent.futures
        toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})
    assert wait_gone(pids(pid_file)[0], seconds=10.0)
    started = [t for t in threading.enumerate() if t not in before]
    for thread in started:
        thread.join(timeout=10)
    assert not [t for t in started if t.is_alive()]


def test_close_during_concurrent_calls_never_breaks_a_call(tmp_path: Path) -> None:
    pid_file = tmp_path / "pids"
    toolbox = McpToolbox(recording(pid_file))
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(5):
                assert toolbox.call("triage_tenders", {"verdict": "GO", "limit": 1})["count"] == 1
        except BaseException as error:  # noqa: BLE001 - collected and asserted below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for _ in range(3):
        toolbox.close()
        time.sleep(0.05)
    for thread in threads:
        thread.join(timeout=60)
    toolbox.close()
    assert errors == []
    assert all(wait_gone(pid) for pid in pids(pid_file))
