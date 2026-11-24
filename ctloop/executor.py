"""Runs pytest and coverage on a copy of the target, contained by macOS sandbox-exec (A1).

Generated tests are arbitrary model-written code, so every run gets:
- a sandbox profile that denies all network access and denies file writes outside
  runs/<id>/ (plus /dev and the per-user temp dirs Python needs),
- a minimal environment (no API keys or tokens; HOME and TMPDIR point at the run folder),
- a timeout, PYTHONDONTWRITEBYTECODE=1, and no pytest cache.
If the sandbox can't be applied we fail closed, unless --unsafe-no-sandbox was given.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ctloop.errors import CtloopError, SandboxError

log = logging.getLogger(__name__)

TAIL_LINES = 40


@dataclass
class ExecResult:
    """Outcome of one contained subprocess."""

    returncode: int
    output: str
    timed_out: bool = False

    def tail(self, lines: int = TAIL_LINES) -> str:
        """Last ``lines`` lines of combined output (for prompts and pr.md)."""
        return "\n".join(self.output.splitlines()[-lines:])


def sandbox_profile(run_dir: Path) -> str:
    """Build the sandbox-exec profile text for ``run_dir``.

    Raises:
        SandboxError: if the path contains characters we can't safely embed.
    """
    root = str(Path(run_dir).resolve())
    if '"' in root or "\\" in root:
        raise SandboxError(f"run folder path can't be embedded in a sandbox profile: {root}")
    return (
        "(version 1)\n"
        "(allow default)\n"
        "(deny network*)\n"
        "(deny file-write*)\n"
        f'(allow file-write* (subpath "{root}") (subpath "/private/var/folders") (regex #"^/dev/"))\n'
    )


class Executor:
    """Runs commands for one run folder, inside the sandbox unless explicitly disabled."""

    def __init__(self, run_dir: Path, *, unsafe_no_sandbox: bool = False) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.unsafe = unsafe_no_sandbox
        self._checked = False
        self._profile_path = self.run_dir / "sandbox.sb"

    def _env(self) -> dict[str, str]:
        tmp = self.run_dir / "tmp"
        tmp.mkdir(exist_ok=True)
        python_bin = str(Path(sys.executable).parent)
        return {
            "PATH": f"{python_bin}:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": str(self.run_dir),
            "TMPDIR": str(tmp),
            "LANG": "en_US.UTF-8",
        }

    def _ensure_sandbox(self) -> None:
        """Write the profile and prove it applies once; fail closed if it doesn't."""
        if self._checked or self.unsafe:
            return
        exe = shutil.which("sandbox-exec")
        if exe is None:
            raise SandboxError("sandbox-exec not found; refusing to run generated tests (use --unsafe-no-sandbox to override)")
        self._profile_path.write_text(sandbox_profile(self.run_dir), encoding="utf-8")
        probe = subprocess.run(
            [exe, "-f", str(self._profile_path), "/usr/bin/true"], capture_output=True, text=True, timeout=30
        )
        if probe.returncode != 0:
            raise SandboxError(f"sandbox profile failed to apply: {probe.stderr.strip() or probe.returncode}")
        self._checked = True

    def run(self, argv: list[str], *, cwd: Path, timeout: float) -> ExecResult:
        """Run ``argv`` in ``cwd`` under the sandbox with a timeout; never raises on test failure."""
        self._ensure_sandbox()
        full = argv if self.unsafe else ["sandbox-exec", "-f", str(self._profile_path), *argv]
        try:
            proc = subprocess.run(
                full, cwd=cwd, env=self._env(), capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as exc:
            out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            return ExecResult(returncode=-1, output=out + f"\nTIMEOUT after {timeout}s", timed_out=True)
        return ExecResult(returncode=proc.returncode, output=proc.stdout + proc.stderr)

    def run_pytest_file(self, work: Path, test_file: str, timeout: float) -> ExecResult:
        """Run one test file alone (so pass/fail is attributable to that file)."""
        return self.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", test_file],
            cwd=work,
            timeout=timeout,
        )

    def run_coverage(self, work: Path, package: str, timeout: float) -> tuple[ExecResult, dict[str, Any]]:
        """Run the copy's whole tests/ folder under coverage and return (pytest result, coverage JSON).

        Raises:
            CtloopError: if coverage produced no JSON report.
        """
        args = ["tests"] if (work / "tests").is_dir() else []
        result = self.run(
            [sys.executable, "-m", "coverage", "run", "--branch", f"--source={package}",
             "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", *args],
            cwd=work,
            timeout=timeout,
        )
        report = self.run(
            [sys.executable, "-m", "coverage", "json", "-q", "-o", "coverage.json"], cwd=work, timeout=timeout
        )
        path = work / "coverage.json"
        if report.returncode != 0 or not path.exists():
            raise CtloopError(f"coverage report failed in {work}:\n{report.tail()}\n{result.tail()}")
        return result, json.loads(path.read_text(encoding="utf-8"))


def summarize_coverage(cov: dict[str, Any]) -> dict[str, Any]:
    """Reduce coverage JSON to the numbers pr.md shows (totals + per file)."""
    totals = cov["totals"]
    return {
        "percent": round(totals["percent_covered"], 1),
        "percent_statements": round(totals.get("percent_statements_covered", totals["percent_covered"]), 1),
        "files": {f: round(d["summary"]["percent_covered"], 1) for f, d in sorted(cov["files"].items())},
    }


_ADDR = re.compile(r"0x[0-9a-f]+")


def failure_signature(output: str) -> str:
    """A stable fingerprint of a pytest failure: the error location and exception lines.

    Used by the no-progress stop (A2): identical signatures across attempts mean the
    fix didn't change anything that matters. Timings and object addresses are dropped.
    """
    keep = [ln.strip() for ln in output.splitlines()
            if ln.startswith("E ") or re.match(r"^\S+\.py:\d+: ", ln)]
    return _ADDR.sub("0x?", "\n".join(keep))

