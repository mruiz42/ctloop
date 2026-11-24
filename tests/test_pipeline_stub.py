"""End to end on a temp copy of the sandbox with the stub provider (real pytest/coverage, no network)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ctloop import cli, stages, workspace
from ctloop.config import RunConfig
from ctloop.state import RunState

from .conftest import STUB_DIR


def _cli(*args: str) -> int:
    return cli.main(list(args))


def _only_run(runs_dir: Path) -> Path:
    (run,) = [p for p in runs_dir.iterdir() if p.is_dir()]
    return run


def test_full_run_raises_coverage_and_quarantines_seeded_bug(target: Path, runs_dir: Path) -> None:
    digest, _ = workspace.tree_hash(target)
    code = _cli("run-all", "--target", str(target), "--runs-dir", str(runs_dir),
                "--provider", "stub", "--stub-dir", str(STUB_DIR))
    assert code == 0
    run = _only_run(runs_dir)
    state = RunState.load(run)
    assert state.after["percent"] > state.baseline["percent"]
    by = {t.qualname: t for t in state.tests}
    assert by["is_leap_year"].status == "quarantined"
    assert by["truncate"].status == "passed" and by["truncate"].attempts == 1
    assert all(t.status == "passed" for q, t in by.items() if q != "is_leap_year")
    assert state.after_suite_ok and state.patch_method == "patch -p1"
    assert (run / "quarantine" / "test_gen_dates_is_leap_year.py").exists()
    patch = (run / "pr" / "changes.patch").read_text()
    assert "test_gen_strings_truncate.py" in patch and "is_leap_year" not in patch
    md = (run / "pr" / "pr.md").read_text()
    assert "sandbox.dates.is_leap_year" in md and "2000" in md
    # Every patched test passes on its own in verify/.
    for t in state.tests:
        if t.status == "passed":
            r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", t.test_file],
                               cwd=run / "verify", capture_output=True, text=True,
                               env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"})
            assert r.returncode == 0, r.stdout
    assert workspace.tree_hash(target)[0] == digest
    assert not list(target.rglob("__pycache__"))


def test_call_cap_exits_3_then_resume_finishes(target: Path, runs_dir: Path) -> None:
    base = ["--runs-dir", str(runs_dir)]
    code = _cli("run-all", "--target", str(target), *base, "--provider", "stub",
                "--stub-dir", str(STUB_DIR), "--max-calls", "4")
    assert code == 3
    run = _only_run(runs_dir)
    state = RunState.load(run)
    assert state.calls_used == 4 and state.stop_reason == "call_cap"
    assert any(t.status == "skipped" for t in state.tests)
    assert (run / "pr" / "pr.md").exists() and "call_cap" in (run / "pr" / "pr.md").read_text()
    code = _cli("run-all", "--resume", run.name, *base, "--max-calls", "30")
    assert code == 0
    state = RunState.load(run)
    assert state.config["max_calls"] == 30 and state.stop_reason is None
    assert {t.status for t in state.tests} == {"passed", "quarantined"}
    assert json.loads((run / "state.json").read_text())["run_id"] == run.name


def test_fix_attempt_cap_quarantines(target: Path, runs_dir: Path, tmp_path: Path) -> None:
    # Each fix returns a different failing test, so no-progress never triggers and the cap does.
    stub = tmp_path / "stub"
    stub.mkdir()
    for p in STUB_DIR.iterdir():
        (stub / p.name).write_text(p.read_text())
    for n in (1, 2, 3):
        (stub / f"dates_is_leap_year.fix{n}.txt").write_text(
            f"```python\nfrom sandbox.dates import is_leap_year\n\ndef test_x():\n"
            f"    assert is_leap_year(2000) is True, 'attempt {n}'\n    assert {n} == {n}\n```\n")
    config = RunConfig(provider="stub", stub_dir=str(stub))
    ctx = stages.create_run(target, None, runs_dir, config)
    assert stages.run_all(ctx) == 0
    rec = ctx.state.record("dates_is_leap_year")
    assert rec.status == "quarantined" and rec.attempts == 3 and "cap" in rec.reason


def test_target_bug_verdict_quarantines_early(target: Path, runs_dir: Path, tmp_path: Path) -> None:
    stub = tmp_path / "stub"
    stub.mkdir()
    for p in STUB_DIR.iterdir():
        (stub / p.name).write_text(p.read_text())
    (stub / "dates_is_leap_year.fix1.txt").write_text("VERDICT: TARGET_BUG 2000 should be a leap year\n")
    ctx = stages.create_run(target, None, runs_dir, RunConfig(provider="stub", stub_dir=str(stub)))
    stages.run_all(ctx)
    rec = ctx.state.record("dates_is_leap_year")
    assert rec.status == "quarantined" and rec.attempts == 1 and "TARGET_BUG" in rec.reason


def test_stage_order_enforced(target: Path, runs_dir: Path) -> None:
    ctx = stages.create_run(target, None, runs_dir, RunConfig(provider="stub", stub_dir=str(STUB_DIR)))
    assert _cli("fix", "--run", ctx.state.run_id, "--runs-dir", str(runs_dir)) == 1


def test_runs_dir_inside_target_rejected(target: Path) -> None:
    assert _cli("gaps", "--target", str(target), "--runs-dir", str(target / "runs"),
                "--provider", "stub", "--stub-dir", str(STUB_DIR)) == 1
    assert not (target / "runs").exists()


def test_stub_without_stub_dir_rejected(target: Path, runs_dir: Path) -> None:
    assert _cli("gaps", "--target", str(target), "--runs-dir", str(runs_dir), "--provider", "stub") == 1
