"""Model providers: headless `claude -p`, a deterministic stub, and a budget wrapper.

All three share one tiny interface, complete(prompt, system) -> Completion, so the
stages never know which one they are talking to.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ctloop.errors import BudgetExhausted, ProviderError

log = logging.getLogger(__name__)

_KEY = re.compile(r"^CTLOOP-KEY: (\S+)", re.MULTILINE)


@dataclass
class Completion:
    """Model reply text and what it cost (USD, 0 for the stub)."""

    text: str
    cost_usd: float = 0.0


class Provider(Protocol):
    """Anything that turns a prompt into a Completion."""

    def complete(self, prompt: str, *, system: str) -> Completion: ...


class StubProvider:
    """Deterministic provider keyed by the prompt's CTLOOP-KEY line (D11).

    Keys look like ``<slug>.write`` or ``<slug>.fixN``. A missing ``fixN`` falls back to
    the latest earlier response for that slug (so "same test again" is the default);
    a missing ``write`` raises ProviderError.
    """

    def __init__(self, responses: dict[str, str], cost_per_call: float = 0.0) -> None:
        self.responses = responses
        self.cost_per_call = cost_per_call

    @classmethod
    def from_dir(cls, directory: Path) -> StubProvider:
        """Load ``<key>.txt`` files from ``directory``.

        Raises:
            ProviderError: if the directory doesn't exist.
        """
        directory = Path(directory)
        if not directory.is_dir():
            raise ProviderError(f"stub dir not found: {directory}")
        return cls({p.name.removesuffix(".txt"): p.read_text(encoding="utf-8") for p in directory.glob("*.txt")})

    def complete(self, prompt: str, *, system: str) -> Completion:
        match = _KEY.search(prompt)
        if match is None:
            raise ProviderError("stub: prompt has no CTLOOP-KEY line")
        slug, _, kind = match.group(1).rpartition(".")
        candidates = [kind]
        if kind.startswith("fix") and kind[3:].isdigit():
            candidates += [f"fix{n}" for n in range(int(kind[3:]) - 1, 0, -1)] + ["write"]
        for k in candidates:
            if f"{slug}.{k}" in self.responses:
                return Completion(self.responses[f"{slug}.{k}"], self.cost_per_call)
        raise ProviderError(f"stub: no response for {slug}.{kind}")


class ClaudeCLIProvider:
    """Calls headless `claude -p` with tools off, the prompt on stdin, and a timeout (D5).

    --safe-mode and --max-turns are optional extras: if the CLI rejects one at argument
    parsing, we warn once, drop it for the rest of the run, and retry the call once.
    That rejected attempt did no model work, so it doesn't count against any cap.
    """

    def __init__(self, *, model: str, timeout: float, max_budget_usd: float, cwd: Path,
                 safe_mode: bool = True, max_turns: bool = True,
                 on_flag_dropped: Callable[[str], None] | None = None) -> None:
        self.model = model
        self.timeout = timeout
        self.max_budget_usd = max_budget_usd
        self.cwd = cwd
        self.flags = {"safe-mode": safe_mode, "max-turns": max_turns}
        self.on_flag_dropped = on_flag_dropped

    def argv(self, system: str) -> list[str]:
        """Build the exact argv (never a shell string)."""
        exe = shutil.which("claude")
        if exe is None:
            raise ProviderError("`claude` CLI not found on PATH; install Claude Code or use --provider stub")
        cmd = [exe, "-p", "--output-format", "json", "--tools", "", "--no-session-persistence",
               "--model", self.model, "--max-budget-usd", str(self.max_budget_usd),
               "--system-prompt", system]
        if self.flags["safe-mode"]:
            cmd.append("--safe-mode")
        if self.flags["max-turns"]:
            cmd += ["--max-turns", "1"]
        return cmd

    def complete(self, prompt: str, *, system: str) -> Completion:
        for _ in range(len(self.flags) + 1):
            try:
                proc = subprocess.run(self.argv(system), input=prompt, capture_output=True, text=True,
                                      timeout=self.timeout, cwd=self.cwd)
            except subprocess.TimeoutExpired as exc:
                log.warning("claude -p timed out after %ss; its cost is unknown and counted as $0", self.timeout)
                raise ProviderError(f"claude -p timed out after {self.timeout}s") from exc
            try:
                data = json.loads(proc.stdout)
            except json.JSONDecodeError:
                dropped = self._drop_rejected_flag(proc)
                if dropped:
                    continue
                raise ProviderError(f"claude -p returned non-JSON (rc={proc.returncode}): "
                                    f"{(proc.stderr or proc.stdout).strip()[:500]}") from None
            if data.get("is_error") or data.get("subtype") != "success":
                raise ProviderError(f"claude -p error: subtype={data.get('subtype')} "
                                    f"result={str(data.get('result'))[:300]}")
            return Completion(str(data.get("result", "")), float(data.get("total_cost_usd") or 0.0))
        raise ProviderError("claude -p kept rejecting its arguments")

    def _drop_rejected_flag(self, proc: subprocess.CompletedProcess[str]) -> str | None:
        if proc.returncode == 0:
            return None
        err = (proc.stderr or "").lower()
        for flag, enabled in self.flags.items():
            if enabled and flag in err:
                self.flags[flag] = False
                log.warning("claude rejected --%s; retrying without it for the rest of this run", flag)
                if self.on_flag_dropped:
                    self.on_flag_dropped(flag)
                return flag
        return None


class BudgetedProvider:
    """Enforces the per-run call cap and dollar cap before every call (D8, D13).

    Counters live in the run state (via the callbacks) so the caps are per run, across resumes.
    """

    def __init__(self, inner: Provider, *, max_calls: int, max_run_usd: float,
                 get_usage: Callable[[], tuple[int, float]], add_usage: Callable[[float], None]) -> None:
        self.inner = inner
        self.max_calls = max_calls
        self.max_run_usd = max_run_usd
        self.get_usage = get_usage
        self.add_usage = add_usage

    def check(self) -> None:
        """Raise BudgetExhausted if the next call would exceed a cap."""
        calls, cost = self.get_usage()
        if calls >= self.max_calls:
            raise BudgetExhausted("call cap")
        if cost >= self.max_run_usd:
            raise BudgetExhausted("spend cap")

    def complete(self, prompt: str, *, system: str) -> Completion:
        self.check()
        try:
            result = self.inner.complete(prompt, system=system)
        except ProviderError:
            self.add_usage(0.0)  # a failed call still used a slot
            raise
        self.add_usage(result.cost_usd)
        return result
