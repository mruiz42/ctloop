"""Stub keys and fallback, budget caps, and the Claude CLI provider against a fake subprocess."""

import json
import subprocess
from pathlib import Path

import pytest

from ctloop import providers
from ctloop.errors import BudgetExhausted, ProviderError
from ctloop.providers import BudgetedProvider, ClaudeCLIProvider, StubProvider


def test_stub_write_and_fix_fallback() -> None:
    stub = StubProvider({"a.write": "W", "a.fix1": "F1"})
    assert stub.complete("CTLOOP-KEY: a.write\n", system="").text == "W"
    assert stub.complete("CTLOOP-KEY: a.fix1\n", system="").text == "F1"
    assert stub.complete("CTLOOP-KEY: a.fix3\n", system="").text == "F1"
    with pytest.raises(ProviderError):
        stub.complete("CTLOOP-KEY: b.write\n", system="")
    with pytest.raises(ProviderError):
        stub.complete("no key", system="")


def _budgeted(inner, max_calls=2, max_run_usd=10.0):
    usage = {"calls": 0, "cost": 0.0}

    def add(cost: float) -> None:
        usage["calls"] += 1
        usage["cost"] += cost

    return BudgetedProvider(inner, max_calls=max_calls, max_run_usd=max_run_usd,
                            get_usage=lambda: (usage["calls"], usage["cost"]), add_usage=add), usage


def test_budget_stops_at_call_cap() -> None:
    p, usage = _budgeted(StubProvider({"a.write": "x"}), max_calls=2)
    p.complete("CTLOOP-KEY: a.write", system="")
    p.complete("CTLOOP-KEY: a.write", system="")
    with pytest.raises(BudgetExhausted) as exc:
        p.complete("CTLOOP-KEY: a.write", system="")
    assert exc.value.reason == "call cap" and usage["calls"] == 2


def test_budget_stops_at_spend_cap() -> None:
    p, usage = _budgeted(StubProvider({"a.write": "x"}, cost_per_call=0.6), max_calls=10, max_run_usd=1.0)
    p.complete("CTLOOP-KEY: a.write", system="")
    p.complete("CTLOOP-KEY: a.write", system="")
    with pytest.raises(BudgetExhausted) as exc:
        p.complete("CTLOOP-KEY: a.write", system="")
    assert exc.value.reason == "spend cap" and usage["cost"] == pytest.approx(1.2)


class FakeRun:
    """Records argv and returns queued CompletedProcess results."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert "shell" not in kwargs and kwargs["input"].startswith("prompt")
        r = self.results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _ok(text="```python\nx=1\n```", cost=0.01):
    out = json.dumps({"result": text, "is_error": False, "subtype": "success", "total_cost_usd": cost})
    return subprocess.CompletedProcess([], 0, out, "")


@pytest.fixture
def claude(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/bin/claude")
    dropped: list[str] = []
    prov = ClaudeCLIProvider(model="sonnet", timeout=5, max_budget_usd=0.25, cwd=tmp_path,
                             on_flag_dropped=dropped.append)
    return prov, dropped


def test_claude_success_returns_cost(monkeypatch, claude) -> None:
    prov, _ = claude
    fake = FakeRun(_ok(cost=0.02))
    monkeypatch.setattr(providers.subprocess, "run", fake)
    result = prov.complete("prompt", system="sys")
    assert result.cost_usd == 0.02 and "x=1" in result.text
    argv = fake.calls[0]
    assert argv[:2] == ["/bin/claude", "-p"] and "--safe-mode" in argv and argv[argv.index("--tools") + 1] == ""
    assert argv[argv.index("--max-budget-usd") + 1] == "0.25"


def test_claude_is_error(monkeypatch, claude) -> None:
    prov, _ = claude
    out = json.dumps({"result": "boom", "is_error": True, "subtype": "error_max_budget_usd"})
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(subprocess.CompletedProcess([], 1, out, "")))
    with pytest.raises(ProviderError, match="error_max_budget_usd"):
        prov.complete("prompt", system="s")


def test_claude_non_json(monkeypatch, claude) -> None:
    prov, _ = claude
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(subprocess.CompletedProcess([], 1, "oops", "bad thing")))
    with pytest.raises(ProviderError, match="non-JSON"):
        prov.complete("prompt", system="s")


def test_claude_timeout(monkeypatch, claude) -> None:
    prov, _ = claude
    monkeypatch.setattr(providers.subprocess, "run", FakeRun(subprocess.TimeoutExpired("claude", 5)))
    with pytest.raises(ProviderError, match="timed out"):
        prov.complete("prompt", system="s")


def test_claude_missing_binary(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(providers.shutil, "which", lambda name: None)
    prov = ClaudeCLIProvider(model="sonnet", timeout=5, max_budget_usd=0.25, cwd=tmp_path)
    with pytest.raises(ProviderError, match="not found"):
        prov.complete("prompt", system="s")


def test_claude_safe_mode_fallback_retries_once_and_is_not_counted(monkeypatch, claude) -> None:
    prov, dropped = claude
    rejected = subprocess.CompletedProcess([], 1, "", "error: unknown option '--safe-mode'")
    fake = FakeRun(rejected, _ok())
    monkeypatch.setattr(providers.subprocess, "run", fake)
    budgeted, usage = _budgeted(prov, max_calls=5)
    budgeted.complete("prompt", system="s")
    assert dropped == ["safe-mode"]
    assert "--safe-mode" in fake.calls[0] and "--safe-mode" not in fake.calls[1]
    assert usage["calls"] == 1  # the argv rejection didn't count
