# Guidance log

| Time | Stage | Decision / guidance |
|---|---|---|
| 14:00 | Brief | Task: converging test loop. Python only, no git/GitHub (a PR is a folder with a patch + pr.md), free/OSS only besides Claude Code, never touch target source, caps on calls/retries, run tests on a copy. 60 min total, demo by min 50, review loop max 5 min. |
| 14:02 | Brief | Model: `claude -p` for real runs + a deterministic stub provider for tests and offline use. |
| 14:02 | Brief | Tooling: uv venv + pytest + coverage.py. |
| 14:02 | Brief | Failing test: fix the test up to the cap, then quarantine it and report it in pr.md as a suspected target bug. |
| 14:02 | Brief | Sandbox gets 1 seeded bug. Default caps: 30 model calls, 3 fix attempts per test. |
