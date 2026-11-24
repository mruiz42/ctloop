# 00 — Brief: Converging Test Loop

Status: **DRAFT, awaiting user approval**. Started 2026-11-24 at 14:00 PST.

## Why
Agent-platform companies need a tool that takes an existing codebase, finds behavior that has no tests, and closes those gaps on its own with tests that actually pass. We are building it as if it will ship to production: safe, capped, resumable, and auditable. The output is a reviewable "PR", not silent changes.

## Goal
One command, pointed at a Python package folder, that:
1. measures the coverage baseline and finds untested behavior (gaps),
2. writes new tests for those gaps (using a model),
3. runs them **on a copy** of the target,
4. fixes failing *tests* until they're green or the cap is hit,
5. "opens a PR" by writing a PR folder with a patch file and a `pr.md` showing coverage before and after.

## How
- **Architecture:** a reusable Python engine (library) with a thin CLI on top, run locally.
- **Stages:** `gaps` → `write` → `run` → `fix` → `pr`. Each stage can run on its own, a run can **resume** from saved state, and one command (`run-all`, or similar) runs every stage end to end.
- **Model provider:** a provider interface. Real runs call headless `claude -p`. Unit tests and an offline/demo mode use a **deterministic stub provider**.
- **Tooling:** a `uv`-managed local `.venv` with `pytest` and `coverage.py`. Everything is free and open source.
- **Target:** a small dummy `sandbox` package that we build ourselves, starting with partial tests and **one deliberately seeded bug**, so the demo shows the "stops cleanly at cap" path. We can add more code later.
- **Failure policy:** a generated test that fails is retried (the *test* is fixed, never the target) up to the cap. If it still fails, it's **quarantined**: left out of the patch and listed in `pr.md` as a "suspected target bug" with its failure output.

## Constraints
- **Never modify target source code.** The tool writes or deletes files only inside its own run folder (for example `runs/<run-id>/`). Generated tests always run on a **copy** of the target.
- **Caps:** at most 30 model calls per run and 3 fix attempts per test by default, plus a per-call timeout. All can be set with CLI flags. Hitting a cap ends the run cleanly, with state saved and a PR folder written that shows what got done.
- Python only (the installed 3.15.2). **No git or GitHub.** "Open a PR" means writing a folder: `<run>/pr/` containing `changes.patch` and `pr.md`, with one PR folder per run.
- No paid tools besides Claude Code.
- Production quality: clear errors, logging, typed and commented code, unit tests for the core logic.

## Time budget
60 minutes in total (14:00 to 15:00). **A working demo by minute 50 (14:50)**, because this is being recorded. **The implement ↔ review loop is capped at 5 minutes** (this overrides the workflow's default of 10). The investigation and the plan should be lean.

## Definition of done
- [ ] One command raises coverage on the sandbox, with every test in the patch passing.
- [ ] A failing generated test is either fixed or stops cleanly at the cap (quarantined and reported).
- [ ] Every run writes a PR folder (`changes.patch` + `pr.md` with coverage before and after).
- [ ] The target's source code is byte-for-byte unchanged after a run.
- [ ] The engine's core logic has its own unit tests, which pass using the stub provider.
- [ ] A README covers setup, the commands, the stages, resume, the caps, and the demo.

## Out of scope
GitHub/git integration, CI, any UI, languages other than Python, mutation testing, and fixing bugs in the target code.
