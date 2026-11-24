"""Code extraction, verdict parsing, failure signatures, and every guard rule."""

from ctloop.executor import failure_signature
from ctloop.guard import check_candidate
from ctloop.prompts import MAX_CONTEXT_CHARS, build_write_prompt, extract_code, parse_verdict

GOOD = "from pkg.m import f\n\ndef test_f():\n    assert f() == 1\n"


def test_extract_single_block() -> None:
    assert extract_code("hi\n```python\nx = 1\n```\nbye") == "x = 1\n"


def test_extract_prefers_last_python_block() -> None:
    text = "```python\na = 1\n```\n```text\nzzz\n```\n```python\nb = 2\n```"
    assert extract_code(text) == "b = 2\n"


def test_extract_none_without_block() -> None:
    assert extract_code("no code here") is None


def test_parse_verdict() -> None:
    assert parse_verdict("VERDICT: TARGET_BUG 2000 is not treated as leap") == "2000 is not treated as leap"
    assert parse_verdict("all good") is None


def test_write_prompt_has_key_and_is_capped() -> None:
    p = build_write_prompt(key="m_f.write", module="pkg.m", qualname="f", source="x" * 20000,
                           header="", missing_lines=[1], example_test="")
    assert p.startswith("CTLOOP-KEY: m_f.write\n")
    assert len(p) < MAX_CONTEXT_CHARS + 500
    assert "truncated by ctloop" in p


def test_failure_signature_ignores_addresses() -> None:
    a = "tests/t.py:4: AssertionError\nE   assert <obj at 0x1234> == 1\n1 failed in 0.01s"
    b = "tests/t.py:4: AssertionError\nE   assert <obj at 0xbeef> == 1\n1 failed in 0.09s"
    assert failure_signature(a) == failure_signature(b) != ""


def test_guard_accepts_good_test() -> None:
    assert check_candidate(GOOD, "pkg.m").ok


def test_guard_accepts_from_package_import_module() -> None:
    assert check_candidate("from pkg import m\n\ndef test_f():\n    assert m.f()\n", "pkg.m").ok


def test_guard_rejects_syntax_error() -> None:
    assert not check_candidate("def (:", "pkg.m").ok


def test_guard_rejects_missing_import() -> None:
    res = check_candidate("def f():\n    return 1\n\ndef test_f():\n    assert f() == 1\n", "pkg.m")
    assert "does not import pkg.m" in res.reasons


def test_guard_rejects_no_assertions() -> None:
    assert "has no assertions" in check_candidate("from pkg.m import f\n\ndef test_f():\n    f()\n", "pkg.m").reasons


def test_guard_rejects_skip_and_xfail() -> None:
    code = GOOD + "\nimport pytest\n\n@pytest.mark.xfail\ndef test_g():\n    pytest.skip('x')\n    assert 1 == 2\n"
    reasons = check_candidate(code, "pkg.m").reasons
    assert "uses xfail" in reasons and "uses skip" in reasons


def test_guard_rejects_assert_true() -> None:
    assert not check_candidate("from pkg.m import f\n\ndef test_f():\n    assert True\n", "pkg.m").ok


def test_guard_rejects_swallowed_assertion() -> None:
    code = "from pkg.m import f\n\ndef test_f():\n    try:\n        assert f() == 2\n    except AssertionError:\n        pass\n"
    assert not check_candidate(code, "pkg.m").ok


def test_guard_rejects_fewer_assertions_on_fix() -> None:
    previous = GOOD + "\ndef test_g():\n    assert f() != 2\n"
    res = check_candidate(GOOD, "pkg.m", previous=previous)
    assert "assertion count dropped (2 -> 1)" in res.reasons
