from __future__ import annotations

import pytest

from public_sector_copilot import demo


def test_demo_default_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main([]) == 0
    out = capsys.readouterr().out
    assert "reports saved: ['tender-brief']" in out and "'primary': 1, 'backup': 0" in out


def test_demo_denied_with_failover(capsys: pytest.CaptureFixture[str]) -> None:
    assert demo.main(["--deny", "--primary-down"]) == 1
    out = capsys.readouterr().out
    assert "reports saved: []" in out and "REFUSED (denied)" in out
    assert "Partial completion" in out and "report was not saved" in out
    assert "'primary': 1, 'backup': 1" in out and "served_by" in out


def test_demo_is_deterministic(capsys: pytest.CaptureFixture[str]) -> None:
    demo.main([])
    first = capsys.readouterr().out
    demo.main([])
    assert capsys.readouterr().out == first
