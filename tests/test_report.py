"""The patch format applies with `patch -p1`, and pr.md has its sections."""

import shutil
import subprocess
from pathlib import Path

import pytest

from ctloop.report import build_patch, render_pr_md
from ctloop.state import RunState, TestRecord


@pytest.mark.skipif(shutil.which("patch") is None, reason="patch binary missing")
def test_patch_creates_new_files(tmp_path: Path) -> None:
    text = build_patch([("tests/test_gen_a.py", "x = 1\ny = 2"), ("tests/test_gen_b.py", "z = 3\n")])
    assert text.startswith("--- /dev/null\n+++ b/tests/test_gen_a.py\n@@ -0,0 +1,2 @@\n")
    (tmp_path / "tests").mkdir()
    proc = subprocess.run(["patch", "-p1", "-d", str(tmp_path)], input=text, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "tests" / "test_gen_a.py").read_text() == "x = 1\ny = 2\n"


def test_pr_md_sections() -> None:
    cov = {"percent": 10.0, "percent_statements": 12.0, "files": {"p/m.py": 10.0}}
    rec = TestRecord("m_f", "p/m.py", "p.m", "f", 1, [2], status="quarantined", attempts=3,
                     reason="cap", failure_tail="E assert False")
    state = RunState("r", "/t", "p", "h", 3, {"provider": "stub", "model": "sonnet", "max_calls": 30,
                     "max_run_usd": 2.0, "max_budget_usd": 0.25, "unsafe_no_sandbox": False},
                     baseline=cov, after=cov, tests=[rec], after_suite_ok=True, patch_method="patch -p1")
    md = render_pr_md(state, integrity_ok=True, file_count=3)
    for section in ("## Summary", "## Coverage", "## Tests added", "## Quarantined: suspected target bugs",
                    "## Skipped", "## Guard rejections", "## Target integrity", "## How to apply"):
        assert section in md
    assert "E assert False" in md and "PASS: 3 files" in md
