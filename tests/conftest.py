"""Shared fixtures: a temp copy of the sandbox target and the demo stub responses."""

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "targets" / "sandbox"
STUB_DIR = ROOT / "demo" / "stub_responses"


@pytest.fixture
def target(tmp_path: Path) -> Path:
    """A private copy of the sandbox, so tests never touch targets/sandbox."""
    dst = tmp_path / "target"
    shutil.copytree(SANDBOX, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"
