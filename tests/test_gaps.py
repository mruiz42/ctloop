"""Gap extraction from coverage JSON, including the A2 untestable prefilter."""

from pathlib import Path

from ctloop.gaps import function_source, parse_gaps

SRC = '''import abc
from typing import Protocol


def used():
    return 1


def gap(x):
    if x:
        return 2
    return 3


class P(Protocol):
    def meth(self):
        ...


class Base(abc.ABC):
    @abc.abstractmethod
    def run(self):
        raise NotImplementedError


if __name__ == "__main__":
    print(gap(1))
'''


def _cov() -> dict:
    return {"files": {"pkg/m.py": {"functions": {
        "": {"start_line": None, "missing_lines": [26, 27]},
        "used": {"start_line": 5, "missing_lines": []},
        "gap": {"start_line": 9, "missing_lines": [11, 12]},
        "P.meth": {"start_line": 16, "missing_lines": [17]},
        "Base.run": {"start_line": 22, "missing_lines": [23]},
    }}}}


def _work(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text(SRC)
    return tmp_path


def test_only_real_gaps_are_returned(tmp_path: Path) -> None:
    gaps, untestable = parse_gaps(_cov(), _work(tmp_path))
    assert [(g.module, g.qualname, g.missing_lines) for g in gaps] == [("pkg.m", "gap", (11, 12))]
    assert gaps[0].slug == "m_gap"
    reasons = {u["qualname"]: u["reason"] for u in untestable}
    assert reasons == {"<module>": "__main__ block", "P.meth": "Protocol class body", "Base.run": "abstract method"}


def test_function_source_includes_decorators(tmp_path: Path) -> None:
    src = function_source(_work(tmp_path), "pkg/m.py", "Base.run")
    assert src.splitlines()[0].strip() == "@abc.abstractmethod"
