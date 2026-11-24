# ctloop: converging test loop

Point it at a Python package folder. It measures coverage, finds functions with untested
lines, asks a model to write tests for them, runs those tests **on a copy** inside a
sandbox, fixes failing *tests* (never the target) up to a cap, and "opens a PR" by
writing `runs/<id>/pr/changes.patch` + `pr.md` with coverage before and after.

Why: agent platforms need a tool that closes test gaps on its own, safely and auditably:
capped, resumable, contained, and producing a reviewable artifact instead of silent edits.

## Setup

```
uv sync                       # Python 3.15, pytest 9.2.0, coverage 7.17.5 (pinned, uv.lock)
uv run pytest                 # engine unit + integration tests (stub provider, no network)
```

Requires macOS (`sandbox-exec`, `patch`) and, for real runs, a logged-in `claude` CLI.

## Demo (deterministic, stub provider)

```
uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses
cat runs/<RUN_ID>/pr/pr.md
cat runs/<RUN_ID>/pr/changes.patch

# cap path: stops cleanly, exit 3, PR folder still written
uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses --max-calls 4
# continue the same run with a higher cap
uv run python -m ctloop run-all --resume <RUN_ID> --max-calls 30

# real model (claude -p)
uv run python -m ctloop run-all --target targets/sandbox --model sonnet --max-calls 10
```

Stub demo result: coverage 28.3% -> 92.5% in 7 calls. `truncate` converges after one
fix; `is_leap_year` (seeded bug: the "divisible by 400" rule is missing, so 2000 is not
a leap year) is **quarantined** and reported as a suspected target bug. The stub
responses live in `demo/stub_responses/<slug>.<write|fixN>.txt`.

## Stages

Each stage runs on its own and is idempotent over `state.json`:

| Command | What it does |
|---|---|
| `gaps --target PATH [--package NAME]` | Creates a run, hashes the target, copies it to `work/`, runs baseline coverage, lists gaps (prints `RUN_ID`). |
| `write --run ID` | One model call per gap function; extracts the fenced code, guards it, writes `work/tests/test_gen_<slug>.py`. |
| `run --run ID` | Runs each generated test file alone (sandboxed, with a timeout). |
| `fix --run ID` | Fixes failing tests up to `--max-fix-attempts`; quarantines what won't converge. |
| `pr --run ID` | Writes `pr/changes.patch` (passed tests only), applies it with `patch -p1` to a fresh `verify/` copy, measures "after" coverage there, writes `pr/pr.md`. |
| `run-all (--target PATH \| --resume ID)` | All of the above. |

Stages run out of order fail with a clear message (`fix` needs `run`, and so on).

## Run folder

```
runs/<id>/state.json  run.log  sandbox.sb  pytest.ini
          calls/NNN-<kind>-<slug>.{prompt,response}.txt   every model exchange, for audit
          work/        copy of the target + generated tests
          verify/      fresh copy + changes.patch applied ("after" coverage)
          quarantine/  tests that never passed
          pr/changes.patch  pr/pr.md
```

## Resume

`run-all --resume ID [flags]` reloads `state.json`. Explicit flags override the stored
config and are saved. Tests skipped because of a cap go back to where they were, so a
capped run can be continued with a higher cap. Call and spend counters are **per run**,
across resumes.

## Caps and flags

| Flag | Default | Meaning |
|---|---|---|
| `--max-calls` | 30 | model calls per run |
| `--max-fix-attempts` | 3 | fix attempts per test |
| `--max-budget-usd` | 0.25 | passed to every `claude -p` call |
| `--max-run-usd` | 2.00 | per run, summed from `total_cost_usd` (can overshoot by at most one call) |
| `--call-timeout` / `--test-timeout` | 120 / 60 s | per model call / per pytest run |
| `--provider` / `--model` / `--stub-dir` | claude / sonnet / - | provider choice |
| `--unsafe-no-sandbox` | off | run tests without sandbox-exec (logged in pr.md) |
| `--runs-dir` | runs | where run folders go |

Budgets are checked before every call. Gaps that can't be served are marked
`skipped (call cap|spend cap)`, the pipeline still runs and writes the PR folder.

Exit codes: `0` completed (quarantines included), `1` error (including a target
modification or a failing combined suite), `2` usage, `3` stopped at a cap.

## Safety guarantees

- **The target is never written.** Its sha256 tree hash is recorded at start and checked
  at the end of every command (`TargetModifiedError` -> exit 1). pr.md shows the result.
- **All writes go through `safe_path`**, which resolves real paths and refuses anything
  outside `runs/<id>/` (`../` and symlink escapes included). Run folders are never reused.
  Nothing outside the run folder is ever deleted.
- **Generated tests run under `sandbox-exec`**: no network, and file writes only in the
  active copy (`work/` or `verify/`) and `runs/<id>/tmp/` (plus `/dev`; system temp is
  not writable). `state.json`, `calls/`, `pr/` and `quarantine/` are not writable by tests.
  The profile is passed inline (`sandbox-exec -p`, by absolute path), so there is no
  profile file to tamper with. Reads of common secret locations (`~/.ssh`, `~/.aws`,
  `~/.gnupg`, `~/.netrc`, `~/.config/gh`, `~/Library/Keychains`, `~/.docker/config.json`)
  are denied. Minimal environment: no API keys or tokens; `HOME`/`TMPDIR` point at
  `runs/<id>/tmp/`; `PYTHONDONTWRITEBYTECODE=1`; no pytest cache. If the sandbox can't be
  applied, the run fails closed.
- **The model has no tools** (`claude -p --tools ""`), the prompt goes on stdin (no shell),
  `cwd` is the run folder and `--safe-mode` keeps the project's CLAUDE.md/skills out. If the
  CLI ever rejects `--safe-mode` or `--max-turns`, it retries once without it, warns, and
  records it in pr.md. Prompts contain only content from inside the target (~8k chars cap).

## Anti-weakening guard and its limits

Prompts say the docstring is the spec and that expected values must never change to match
output; the model may reply `VERDICT: TARGET_BUG <reason>` instead. Every candidate is
AST-checked: it must parse, import the module under test, have at least one assertion,
use no skip/xfail, no `assert True`, no swallowed `AssertionError`, and a fix may not
have fewer assertions than the previous version. A fix whose failure is identical to the
previous attempt is quarantined right away (`no progress`). Untestable code (`__main__`
blocks, abstract methods, Protocol bodies, `pass`/`...` bodies) is skipped without a call.

**Not caught:** a fix that changes an expected value to match buggy output. That is why
quarantined and added tests are listed in pr.md for human review.

## Known risks

- **Other reads are still allowed.** Generated tests can read any file not on the deny
  list above, and their output (failure tail) is sent to the model in the fix prompt and
  copied into pr.md. Don't run this on a machine with secrets in other locations.
- **The spend cap can overshoot by at most one call** (one `--max-budget-usd`, about
  $0.25), because the cap is checked before each call, not during it.

## Known limits / next steps

- macOS only (sandbox-exec). One process per run id; no parallel test runs.
- Only new test files are added; existing tests are never edited.
- Next: `--json-schema` output, Linux sandboxing, Evolve-stage artifacts
  (`feedback/submissions.jsonl`, `evals/`) were deferred by decision.
