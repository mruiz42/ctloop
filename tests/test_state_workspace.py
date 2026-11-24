"""State round trip, tree hash, and the safe_path guard (A4)."""

from pathlib import Path

import pytest

from ctloop import workspace
from ctloop.errors import PathEscapeError, TargetModifiedError
from ctloop.state import RunState, TestRecord


def test_state_round_trip(tmp_path: Path) -> None:
    state = RunState("r1", "/t", "pkg", "abc", 3, {"max_calls": 5},
                     tests=[TestRecord("m_f", "pkg/m.py", "pkg.m", "f", 1, [2, 3])])
    state.save(tmp_path)
    loaded = RunState.load(tmp_path)
    assert loaded == state
    assert not (tmp_path / "state.json.tmp").exists()


def test_tree_hash_detects_one_byte_change(target: Path) -> None:
    digest, count = workspace.tree_hash(target)
    assert count >= 5
    workspace.verify_untouched(target, digest)
    f = target / "sandbox" / "strings.py"
    f.write_bytes(f.read_bytes() + b" ")
    with pytest.raises(TargetModifiedError):
        workspace.verify_untouched(target, digest)


def test_tree_hash_ignores_pycache(target: Path) -> None:
    digest, _ = workspace.tree_hash(target)
    (target / "sandbox" / "__pycache__").mkdir()
    (target / "sandbox" / "__pycache__" / "x.pyc").write_bytes(b"x")
    assert workspace.tree_hash(target)[0] == digest


def test_safe_path_blocks_dotdot_escape(tmp_path: Path) -> None:
    run = workspace.create_run_dir(tmp_path / "runs", "r1")
    assert workspace.safe_path(run, run / "work" / "a.py").is_relative_to(run)
    with pytest.raises(PathEscapeError):
        workspace.safe_path(run, run / ".." / ".." / "evil.txt")


def test_safe_path_blocks_symlink_escape(tmp_path: Path) -> None:
    run = workspace.create_run_dir(tmp_path / "runs", "r1")
    outside = tmp_path / "outside"
    outside.mkdir()
    (run / "link").symlink_to(outside)
    with pytest.raises(PathEscapeError):
        workspace.write_file(run, run / "link" / "x.txt", "nope")
    assert not (outside / "x.txt").exists()


def test_run_dir_never_reused(tmp_path: Path) -> None:
    workspace.create_run_dir(tmp_path / "runs", "r1")
    with pytest.raises(FileExistsError):
        workspace.create_run_dir(tmp_path / "runs", "r1")
