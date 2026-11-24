"""Persisted run state (runs/<id>/state.json), saved atomically after every change.

Stages are idempotent over this state: each test record's status says what work is
left, so a crashed or capped run resumes where it stopped.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"


class Status(StrEnum):
    """Lifecycle of one gap's generated test."""

    PENDING = "pending"  # no test yet
    WRITTEN = "written"  # test file written, not run yet
    PASSED = "passed"
    FAILED = "failed"  # last run failed (or the candidate was rejected)
    QUARANTINED = "quarantined"  # gave up: suspected target bug
    SKIPPED = "skipped"  # not served because a cap was hit


@dataclass
class TestRecord:
    """One gap (a function with missing lines) and the test generated for it."""

    __test__ = False  # stop pytest from trying to collect this class

    slug: str
    file: str  # relative source path, e.g. sandbox/strings.py
    module: str  # dotted module, e.g. sandbox.strings
    qualname: str
    start_line: int
    missing_lines: list[int]
    status: str = Status.PENDING
    attempts: int = 0  # fix attempts used
    test_file: str | None = None  # path relative to work/
    code: str = ""
    failure_tail: str = ""
    signature: str = ""
    reason: str = ""
    skipped_from: str | None = None  # status to restore when a capped run resumes
    history: list[str] = field(default_factory=list)


@dataclass
class RunState:
    """Everything a run needs to resume and to write its PR folder."""

    run_id: str
    target: str
    package: str
    target_hash: str
    target_file_count: int
    config: dict[str, Any]
    stages_done: list[str] = field(default_factory=list)
    baseline: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    tests: list[TestRecord] = field(default_factory=list)
    untestable: list[dict[str, Any]] = field(default_factory=list)
    calls_used: int = 0
    cost_usd: float = 0.0
    safe_mode_supported: bool = True
    max_turns_supported: bool = True
    stop_reason: str | None = None
    after_suite_ok: bool | None = None
    patch_method: str | None = None
    warnings: list[str] = field(default_factory=list)

    def save(self, run_dir: Path) -> None:
        """Write state.json atomically (tmp file + os.replace) so a crash never leaves half a file."""
        path = run_dir / STATE_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, run_dir: Path) -> RunState:
        """Load state.json from ``run_dir``.

        Raises:
            FileNotFoundError: if the run folder has no state.json.
        """
        data = json.loads((run_dir / STATE_FILE).read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in data.items() if k in known}
        data["tests"] = [TestRecord(**t) for t in data.get("tests", [])]
        return cls(**data)

    def record(self, slug: str) -> TestRecord:
        """Return the record for ``slug`` (KeyError if absent)."""
        for rec in self.tests:
            if rec.slug == slug:
                return rec
        raise KeyError(slug)


def new_run_id() -> str:
    """Timestamp plus random suffix, so two runs started in the same second don't collide."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
