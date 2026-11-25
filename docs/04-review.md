# 04 - Review (round 1)

## Open questions
None. The fix for the blocker is a contained change and needs no user decision.

## Verdict
CHANGES REQUESTED. There is one blocker: the A1 containment can be bypassed. Everything else holds well enough for the demo.

## Pass 1: Big picture

**B1 (blocker): sandboxed test code can rewrite its own sandbox profile, then escape it.** `ctloop/executor.py:56` allows writes to the whole run folder (`subpath root`), and the profile file itself lives in that folder (`executor.py:69`, `self._profile_path = self.run_dir / "sandbox.sb"`). `sandbox-exec -f` reads that file again on every call (`executor.py:96`). I reproduced it with the real `Executor`:
- `touch <outside>/a` under the sandbox gives rc=1 (blocked, which is correct).
- Sandboxed `sh -c 'printf "(version 1)(allow default)" > <run>/sandbox.sb'` gives rc=0.
- `touch <outside>/b` then gives rc=0, and the file is created outside the run folder.

So a generated test (model-written code) running at write or fix time can, by itself, unlock every later pytest run in the same process (the next fix attempt, the `verify/` coverage run). Those runs can then write anywhere, including the target. The final `verify_untouched` only notices a change to the target after the damage is done. It doesn't notice writes anywhere else, such as `~` or the repo. This breaks the brief's main constraint ("never modify target source", "writes only inside its own run folder") and A1.
- **Fix:** (a) Keep the profile out of reach of the sandboxed process. Pass it inline with `sandbox-exec -p <profile-text>` instead of `-f`, or write it once to a path that isn't writable. (b) Narrow the writable subpaths from the whole run folder to `work/`, `verify/`, and `tmp/` only. Then a test also can't tamper with `state.json`, `calls/`, `quarantine/`, or `pr/changes.patch`/`pr.md`, which are the audit trail (see B2). (c) Add a unit test: a sandboxed command writes to the profile path, and to `state.json`, and must get rc != 0.

**B2 (major, same root cause as B1): generated tests can forge the audit trail and the PR.** With the whole run folder writable (`executor.py:56`), a test running from `work/` can overwrite `../state.json` (for example, mark records `passed` or reset `calls_used`, which defeats the call cap on resume), `../calls/*`, or a `pr/changes.patch` left from an earlier run. The fix is B1(b). Nothing more is needed.

Everything else in the big picture holds:
- Fit with the brief: all 5 stages, `run-all`, resume, caps that exit 3, quarantine listed in pr.md, the PR folder, and the stub plus a real provider are present. Nothing is built beyond scope.
- A4 `safe_path` (`workspace.py:61-73`): it resolves real paths and checks that the path is the root or has the root as a parent. Every write, move, and rmtree in the tool goes through it. Bounded.
- Target never modified by the tool itself: the target is only read (`tree_hash`, `copytree`). The `validate_target` overlap check (`stages.py:110`) stops `--runs-dir` from sitting inside the target, or the target inside it.
- Caps: the check happens before each call, failed calls still count, and the counters live in state, so they carry across resumes. The spend cap can overshoot by at most one call's `--max-budget-usd` (0.25). That's acceptable, but say so in the README.
- Resume and quarantine: `skipped_from` restore, persisted attempts, the no-progress signature, and the guard's assertion-count check are coherent.

## Pass 2: Details

| # | file:line | Severity | Failure scenario | Fix |
|---|---|---|---|---|
| D1 | `ctloop/executor.py:96` | minor | `run()` calls `sandbox-exec` by bare name, resolved through the child's PATH, while `_ensure_sandbox` checks the path from `shutil.which`. A different `sandbox-exec` earlier on PATH would be used without that check. | Store the `exe` path from `_ensure_sandbox` and use it in `run()`. (Fold this into the B1 change.) |
| D2 | `ctloop/executor.py:54` with `stages.py:280` | minor | The profile has `allow default` for reads. A generated test can `print(open(os.path.expanduser("~/.aws/credentials")...))`. The output goes into `failure_tail`, which is sent in the fix prompt to `claude -p` and copied into pr.md. A1 didn't require read denial, so this isn't a blocker. | Optional: `(deny file-read* (subpath "<real HOME>"))` except for paths the venv needs, or note it as a residual risk in the README. |
| D3 | `ctloop/stages.py:362-366` | minor | `stage_pr` writes pr.md and marks the stage done, then `verify_untouched` raises. That's correct (exit 1, and pr.md shows the integrity check failed). I'm only noting that I confirmed it's intended. | None. |

## Decisions questioned
- **A1 sandbox-exec containment:** the approach holds. The way it's built doesn't (B1/B2): the writable area has to exclude the profile and the tool's own state.
- **A4 safe_path for every tool write:** holds. I checked every rmtree, move, and write call site.
- **Fail closed when sandbox-exec is missing or the probe fails:** holds.
- **Measuring "after" coverage on a fresh `verify/` copy with only the passed tests:** holds. The patch is tested on its own.
- **Exit 3 when a cap is hit:** holds.
- **Guard as a cheap AST check:** holds as a first line of defense. Changed expected values remain a documented residual risk.

---

# Round 2 (commit 0819a2b)

## Open questions
None.

## Verdict
**APPROVE.** This verdict replaces the Round 1 verdict above.

## Previous findings
I checked each one against the real `Executor` with cwd=`verify/` and the venv python.
- **B1 fixed.** The profile is passed inline (`sandbox-exec -p`, `executor.py:134`), so there's no profile file left to rewrite.
- **B2 fixed.** Writing `../state.json` from `verify/` gives PermissionError, and state.json is unchanged. Writes inside cwd still work (that's where coverage.json goes), and `tempfile.mkstemp()` works through TMPDIR=runs/<id>/tmp. `_profile` rejects cwd == run folder, and any cwd outside the run folder.
- **D1 fixed.** `run()` now uses the absolute path from `_ensure_sandbox`.
- **D2 fixed (partial, as intended).** Reading `~/.ssh/known_hosts` gives PermissionError. The deny list isn't exhaustive, and the README Known risks section says so.

## New blocker or major findings
None. The full suite passes (50 tests, 9.7s), and no regression showed up in the verify/ or coverage paths.
