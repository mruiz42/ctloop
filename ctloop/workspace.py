"""Filesystem safety: the target is only ever read and hashed; all writes go through safe_path.

A4: every write, move, or delete the tool makes must resolve inside runs/<id>/.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from ctloop.errors import PathEscapeError, TargetModifiedError

# Caches that Python/pytest/coverage could create; excluded from both the hash and the copy.
_IGNORED = ("__pycache__", ".pytest_cache", ".coverage*", "*.pyc", ".DS_Store")


def _ignored(name: str) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(name, pat) for pat in _IGNORED)


def tree_hash(root: Path) -> tuple[str, int]:
    """Hash every file under ``root`` (sorted relative path + bytes; symlinks by target text).

    Returns:
        (sha256 hex digest, number of files hashed).
    """
    digest = hashlib.sha256()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not _ignored(d))
        for name in sorted(filenames):
            if _ignored(name):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            digest.update(rel.encode() + b"\0")
            if path.is_symlink():
                digest.update(b"L" + os.readlink(path).encode())
            else:
                digest.update(path.read_bytes())
            digest.update(b"\0")
            count += 1
    return digest.hexdigest(), count


def verify_untouched(target: Path, expected_hash: str) -> None:
    """Raise TargetModifiedError if ``target`` no longer hashes to ``expected_hash``."""
    actual, _ = tree_hash(target)
    if actual != expected_hash:
        raise TargetModifiedError(
            f"target {target} changed during the run (hash {expected_hash[:12]} -> {actual[:12]})"
        )


def safe_path(run_dir: Path, path: Path | str) -> Path:
    """Resolve ``path`` and return it only if it lies inside ``run_dir``.

    Resolving real paths means `../` tricks and symlinks pointing outside are both caught.

    Raises:
        PathEscapeError: if the resolved path is outside the run folder.
    """
    root = Path(run_dir).resolve()
    resolved = Path(path).resolve()
    if resolved != root and root not in resolved.parents:
        raise PathEscapeError(f"refusing to touch {path}: outside run folder {root}")
    return resolved


def create_run_dir(runs_dir: Path, run_id: str) -> Path:
    """Create a fresh run folder. Never reuses an existing one (exist_ok=False)."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / run_id
    run_dir.mkdir(exist_ok=False)
    # An empty pytest config at the run root stops pytest's upward config search here,
    # so the project's own pyproject.toml never affects runs on the copies.
    (run_dir / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    return run_dir.resolve()


def copy_target(target: Path, run_dir: Path, name: str) -> Path:
    """Copy ``target`` to ``run_dir/name`` (replacing an older copy there). Links stay links."""
    dst = safe_path(run_dir, run_dir / name)
    if dst.exists():
        remove_tree(run_dir, dst)
    shutil.copytree(target, dst, symlinks=True, ignore=shutil.ignore_patterns(*_IGNORED))
    return dst


def remove_tree(run_dir: Path, path: Path) -> None:
    """Delete a directory, but only inside the run folder."""
    shutil.rmtree(safe_path(run_dir, path))


def write_file(run_dir: Path, path: Path, text: str) -> Path:
    """Write ``text`` to ``path`` inside the run folder, creating parents."""
    dst = safe_path(run_dir, path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8")
    return dst


def move_file(run_dir: Path, src: Path, dst: Path) -> Path:
    """Move a file; both ends must be inside the run folder."""
    s, d = safe_path(run_dir, src), safe_path(run_dir, dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(s, d)
    return d
