"""A1: generated code can't write outside the run folder or open a network socket."""

import sys
from pathlib import Path

import pytest

from ctloop import workspace
from ctloop.errors import SandboxError
from ctloop.executor import Executor, sandbox_profile

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS only")


def _run(run: Path, code: str):
    work = run / "work"
    work.mkdir(exist_ok=True)
    return Executor(run).run([sys.executable, "-c", code], cwd=work, timeout=30)


def test_write_inside_work_and_tmp_allowed(tmp_path: Path) -> None:
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    res = _run(run, f"open({str(run / 'work' / 'ok.txt')!r}, 'w').write('x');"
                    f"open({str(run / 'tmp' / 'ok.txt')!r}, 'w').write('x')")
    assert res.returncode == 0, res.output
    assert (run / "work" / "ok.txt").exists() and (run / "tmp" / "ok.txt").exists()


def test_tool_state_not_writable(tmp_path: Path) -> None:
    """B2: a test can't forge state.json, calls/, pr/ or quarantine/."""
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    for name in ("state.json", "calls/x.json", "pr/pr.md", "quarantine/x.py"):
        (run / name).parent.mkdir(parents=True, exist_ok=True)
        res = _run(run, f"open({str(run / name)!r}, 'w').write('forged')")
        assert res.returncode != 0 and "Operation not permitted" in res.output, name
        assert not (run / name).exists() or (run / name).read_text() != "forged"


def test_no_profile_file_to_tamper_with(tmp_path: Path) -> None:
    """B1: writing a permissive profile into the run folder doesn't loosen the next run."""
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    ex = Executor(run)
    work = run / "work"
    work.mkdir()
    loose = "(version 1)(allow default)"
    ex.run([sys.executable, "-c", f"open({str(run / 'sandbox.sb')!r}, 'w').write({loose!r})"],
           cwd=work, timeout=30)
    ex.run([sys.executable, "-c", f"open({str(work / 'sandbox.sb')!r}, 'w').write({loose!r})"],
           cwd=work, timeout=30)
    outside = run / "escaped.txt"
    res = ex.run([sys.executable, "-c", f"open({str(outside)!r}, 'w').write('x')"], cwd=work, timeout=30)
    assert res.returncode != 0 and not outside.exists()
    assert not (run / "sandbox.sb").exists()


def test_secret_reads_denied(tmp_path: Path) -> None:
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    ssh = Path.home() / ".ssh"
    res = _run(run, f"import os; os.listdir({str(ssh)!r})")
    assert res.returncode != 0 and ("Operation not permitted" in res.output or "No such file" in res.output)


def test_write_outside_run_blocked(tmp_path: Path) -> None:
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    outside = Path.home() / f".ctloop-sandbox-probe-{run.name}"
    res = _run(run, f"open({str(outside)!r}, 'w').write('x')")
    assert res.returncode != 0 and "Operation not permitted" in res.output
    assert not outside.exists()


def test_network_blocked(tmp_path: Path) -> None:
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    res = _run(run, "import socket; socket.create_connection(('1.1.1.1', 80), timeout=3)")
    assert res.returncode != 0 and "Operation not permitted" in res.output


def test_env_has_no_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    res = _run(run, "import os; print(sorted(os.environ))")
    assert "ANTHROPIC_API_KEY" not in res.output


def test_missing_sandbox_exec_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from ctloop import executor
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    run = workspace.create_run_dir(tmp_path / "runs", "r")
    with pytest.raises(SandboxError):
        _run(run, "print(1)")


def test_profile_rejects_quote_in_path() -> None:
    with pytest.raises(SandboxError):
        sandbox_profile([Path('/tmp/a"b')])
