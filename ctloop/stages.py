"""Pipeline orchestration: gaps -> write -> run -> fix -> pr, each idempotent over RunState.

Every stage persists state after each test transition, so a crash or a cap leaves a
resumable run. Every model prompt and response is saved under calls/ for audit.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ctloop import gaps as gapmod
from ctloop import prompts, workspace
from ctloop.config import RunConfig
from ctloop.errors import BudgetExhausted, CtloopError, ProviderError, StageOrderError
from ctloop.executor import Executor, failure_signature, summarize_coverage
from ctloop.guard import check_candidate
from ctloop.providers import BudgetedProvider, ClaudeCLIProvider, Provider, StubProvider
from ctloop.report import build_patch, render_pr_md
from ctloop.state import RunState, Status, TestRecord, new_run_id

log = logging.getLogger(__name__)

CAP_REASONS = ("call cap", "spend cap")
REQUIRES = {"write": "gaps", "run": "write", "fix": "run", "pr": "run"}


@dataclass
class RunContext:
    """A loaded run: its folder, state, config, and lazily-built provider/executor."""

    run_dir: Path
    state: RunState
    config: RunConfig
    provider: Provider | None = None
    _executor: Executor | None = field(default=None, repr=False)

    @property
    def work(self) -> Path:
        return self.run_dir / "work"

    @property
    def target(self) -> Path:
        return Path(self.state.target)

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            self._executor = Executor(self.run_dir, unsafe_no_sandbox=self.config.unsafe_no_sandbox)
        return self._executor

    def save(self) -> None:
        self.state.save(self.run_dir)

    def budgeted(self) -> BudgetedProvider:
        """The provider wrapped with this run's caps (built on first use)."""
        if self.provider is None:
            self.provider = build_provider(self)
        return BudgetedProvider(self.provider, max_calls=self.config.max_calls,
                                max_run_usd=self.config.max_run_usd,
                                get_usage=lambda: (self.state.calls_used, self.state.cost_usd),
                                add_usage=self._add_usage)

    def _add_usage(self, cost: float) -> None:
        self.state.calls_used += 1
        self.state.cost_usd += cost
        self.save()

    def require(self, stage: str) -> None:
        """Raise StageOrderError if the prerequisite stage hasn't run."""
        need = REQUIRES.get(stage)
        if need and need not in self.state.stages_done:
            raise StageOrderError(f"stage '{stage}' needs '{need}' first (run `ctloop {need}`)")

    def done(self, stage: str) -> None:
        if stage not in self.state.stages_done:
            self.state.stages_done.append(stage)
        self.save()


def build_provider(ctx: RunContext) -> Provider:
    """Build the configured provider. The Claude CLI runs with cwd = the run folder (D5)."""
    cfg = ctx.config
    if cfg.provider == "stub":
        return StubProvider.from_dir(Path(cfg.stub_dir or ""))

    def dropped(flag: str) -> None:
        if flag == "safe-mode":
            ctx.state.safe_mode_supported = False
        else:
            ctx.state.max_turns_supported = False
        ctx.state.warnings.append(f"claude rejected --{flag}; continued without it")
        ctx.save()

    return ClaudeCLIProvider(model=cfg.model, timeout=cfg.call_timeout, max_budget_usd=cfg.max_budget_usd,
                             cwd=ctx.run_dir, safe_mode=ctx.state.safe_mode_supported,
                             max_turns=ctx.state.max_turns_supported, on_flag_dropped=dropped)


def validate_target(target: Path, package: str | None, runs_dir: Path) -> tuple[Path, str]:
    """Check the target folder and work out the package name.

    Raises:
        CtloopError: if the target is missing, has no package, or overlaps the runs folder.
    """
    target = target.resolve()
    runs = runs_dir.resolve()
    if not target.is_dir():
        raise CtloopError(f"target not found or not a directory: {target}")
    if target == runs or runs in target.parents or target in runs.parents:
        raise CtloopError("the target and --runs-dir must not contain each other")
    if package is None:
        found = [p.name for p in target.iterdir() if (p / "__init__.py").is_file() and p.name != "tests"]
        if len(found) != 1:
            raise CtloopError(f"can't infer the package in {target} (found {found}); pass --package")
        package = found[0]
    if not (target / package / "__init__.py").is_file():
        raise CtloopError(f"{target / package} is not a Python package (no __init__.py)")
    return target, package


def create_run(target: Path, package: str | None, runs_dir: Path, config: RunConfig) -> RunContext:
    """Create a new run folder and its state (no stage work yet)."""
    config.validate()
    target, package = validate_target(target, package, runs_dir)
    digest, count = workspace.tree_hash(target)
    run_id = new_run_id()
    run_dir = workspace.create_run_dir(runs_dir, run_id)
    state = RunState(run_id=run_id, target=str(target), package=package, target_hash=digest,
                     target_file_count=count, config=config.to_dict())
    ctx = RunContext(run_dir, state, config)
    ctx.save()
    log.info("created run %s for %s (package %s, %d files)", run_id, target, package, count)
    return ctx


def load_run(runs_dir: Path, run_id: str, overrides: dict) -> RunContext:
    """Load a run and apply explicit flag overrides (saved back to state, D9)."""
    run_dir = (runs_dir / run_id).resolve()
    if not (run_dir / "state.json").is_file():
        raise CtloopError(f"no run {run_id} in {runs_dir}")
    state = RunState.load(run_dir)
    config = RunConfig.from_dict(state.config).merged(overrides)
    config.validate()
    state.config = config.to_dict()
    ctx = RunContext(run_dir, state, config)
    ctx.save()
    return ctx


# ---------------------------------------------------------------- stages


def stage_gaps(ctx: RunContext) -> None:
    """Copy the target, measure baseline coverage, and record the gaps."""
    if "gaps" in ctx.state.stages_done:
        return
    workspace.copy_target(ctx.target, ctx.run_dir, "work")
    result, cov = ctx.executor.run_coverage(ctx.work, ctx.state.package, ctx.config.test_timeout)
    if result.returncode not in (0, 5):
        ctx.state.warnings.append(f"the target's existing tests did not pass (pytest rc={result.returncode})")
    ctx.state.baseline = summarize_coverage(cov)
    found, untestable = gapmod.parse_gaps(cov, ctx.work)
    ctx.state.tests = [TestRecord(slug=g.slug, file=g.file, module=g.module, qualname=g.qualname,
                                  start_line=g.start_line, missing_lines=list(g.missing_lines)) for g in found]
    ctx.state.untestable = untestable
    ctx.done("gaps")


def _example_test(work: Path) -> str:
    tests = sorted(p for p in (work / "tests").glob("test_*.py") if not p.name.startswith("test_gen_")) \
        if (work / "tests").is_dir() else []
    return tests[0].read_text(encoding="utf-8") if tests else "# (no existing tests)"


def _write_prompt(ctx: RunContext, rec: TestRecord, kind: str) -> str:
    return prompts.build_write_prompt(
        key=f"{rec.slug}.{kind}", module=rec.module, qualname=rec.qualname,
        source=gapmod.function_source(ctx.work, rec.file, rec.qualname),
        header=gapmod.module_header(ctx.work, rec.file), missing_lines=rec.missing_lines,
        example_test=_example_test(ctx.work))


def _call(ctx: RunContext, provider: BudgetedProvider, rec: TestRecord, kind: str, prompt: str) -> str:
    """One model call with its prompt/response saved under calls/."""
    provider.check()  # don't save a prompt for a call the cap will refuse
    n = ctx.state.calls_used + 1
    base = ctx.run_dir / "calls" / f"{n:03d}-{kind}-{rec.slug}"
    workspace.write_file(ctx.run_dir, base.with_suffix(".prompt.txt"), prompt)
    text = provider.complete(prompt, system=prompts.SYSTEM_PROMPT).text
    workspace.write_file(ctx.run_dir, base.with_suffix(".response.txt"), text)
    return text


def _skip_for_cap(ctx: RunContext, rec: TestRecord, reason: str) -> None:
    rec.skipped_from = rec.status
    rec.status = Status.SKIPPED
    rec.reason = reason
    ctx.state.stop_reason = reason.replace(" ", "_")
    ctx.save()


def _accept_candidate(ctx: RunContext, rec: TestRecord, text: str, previous: str | None) -> bool:
    """Extract and guard a reply; write the test file if it's acceptable."""
    code = prompts.extract_code(text)
    if code is None:
        rec.history.append("no fenced code block in reply")
        rec.status, rec.failure_tail = Status.FAILED, "reply had no ```python block"
        return False
    verdict = check_candidate(code, rec.module, previous)
    if not verdict.ok:
        rec.history.append("guard rejected: " + "; ".join(verdict.reasons))
        rec.status, rec.failure_tail = Status.FAILED, "guard rejected: " + "; ".join(verdict.reasons)
        return False
    rec.code = code
    rec.test_file = f"tests/test_gen_{rec.slug}.py"
    workspace.write_file(ctx.run_dir, ctx.work / rec.test_file, code)
    rec.status = Status.WRITTEN
    return True


def stage_write(ctx: RunContext) -> None:
    """Ask the provider for one test per pending gap."""
    ctx.require("write")
    provider = ctx.budgeted()
    for rec in ctx.state.tests:
        if rec.status != Status.PENDING:
            continue
        try:
            text = _call(ctx, provider, rec, "write", _write_prompt(ctx, rec, "write"))
        except BudgetExhausted as exc:
            _skip_for_cap(ctx, rec, exc.reason)
            continue
        except ProviderError as exc:
            rec.status, rec.failure_tail = Status.FAILED, f"provider error: {exc}"
            rec.history.append(f"write: provider error: {exc}")
            log.warning("write %s: %s", rec.slug, exc)
            ctx.save()
            continue
        _accept_candidate(ctx, rec, text, None)
        rec.history.append(f"write: {rec.status}")
        ctx.save()
    ctx.done("write")


def _run_one(ctx: RunContext, rec: TestRecord) -> None:
    result = ctx.executor.run_pytest_file(ctx.work, rec.test_file or "", ctx.config.test_timeout)
    rec.status = Status.PASSED if result.returncode == 0 else Status.FAILED
    rec.failure_tail = "" if rec.status == Status.PASSED else result.tail()
    rec.signature = "" if rec.status == Status.PASSED else failure_signature(result.output)
    ctx.save()


def stage_run(ctx: RunContext) -> None:
    """Run each written test file alone."""
    ctx.require("run")
    for rec in ctx.state.tests:
        if rec.status == Status.WRITTEN:
            _run_one(ctx, rec)
            log.info("run %s: %s", rec.slug, rec.status)
    ctx.done("run")


def _quarantine(ctx: RunContext, rec: TestRecord, reason: str) -> None:
    if rec.test_file and (ctx.work / rec.test_file).exists():
        workspace.move_file(ctx.run_dir, ctx.work / rec.test_file, ctx.run_dir / "quarantine" / Path(rec.test_file).name)
    rec.status, rec.reason = Status.QUARANTINED, reason
    rec.history.append(f"quarantined: {reason}")
    log.warning("quarantined %s: %s", rec.slug, reason)
    ctx.save()


def _fix_one(ctx: RunContext, provider: BudgetedProvider, rec: TestRecord) -> None:
    while rec.status == Status.FAILED and rec.attempts < ctx.config.max_fix_attempts:
        kind = f"fix{rec.attempts + 1}"
        prompt = prompts.build_fix_prompt(key=f"{rec.slug}.{kind}", write_prompt=_write_prompt(ctx, rec, kind),
                                          test_code=rec.code, failure_tail=rec.failure_tail)
        try:
            text = _call(ctx, provider, rec, kind, prompt)
        except BudgetExhausted as exc:
            _skip_for_cap(ctx, rec, exc.reason)
            return
        except ProviderError as exc:
            rec.attempts += 1
            rec.history.append(f"{kind}: provider error: {exc}")
            ctx.save()
            continue
        rec.attempts += 1
        reason = prompts.parse_verdict(text)
        if reason is not None and prompts.extract_code(text) is None:
            _quarantine(ctx, rec, f"model verdict TARGET_BUG: {reason}")
            return
        previous_sig = rec.signature
        if not _accept_candidate(ctx, rec, text, rec.code or None):
            ctx.save()
            continue
        _run_one(ctx, rec)
        rec.history.append(f"{kind}: {rec.status}")
        if rec.status == Status.FAILED and previous_sig and rec.signature == previous_sig:
            _quarantine(ctx, rec, "no progress: same failure as the previous attempt")
            return
    if rec.status == Status.FAILED:
        _quarantine(ctx, rec, f"still failing after {rec.attempts} fix attempts (cap)")


def stage_fix(ctx: RunContext) -> None:
    """Fix failing tests (never the target), quarantining the ones that won't converge."""
    ctx.require("fix")
    provider = ctx.budgeted()
    for rec in ctx.state.tests:
        if rec.status == Status.FAILED:
            _fix_one(ctx, provider, rec)
    ctx.done("fix")


def _apply_patch(ctx: RunContext, patch_file: Path, verify: Path, passed: list[TestRecord]) -> str:
    """Apply changes.patch to verify/ with `patch -p1`; copy the files if `patch` is missing (D10)."""
    exe = shutil.which("patch")
    if exe is None:
        for rec in passed:
            workspace.write_file(ctx.run_dir, verify / (rec.test_file or ""), rec.code)
        return "file copy (patch binary not found)"
    with patch_file.open("rb") as fh:
        proc = subprocess.run([exe, "-p1", "-d", str(verify)], stdin=fh, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise CtloopError(f"changes.patch did not apply cleanly:\n{proc.stdout}{proc.stderr}")
    return "patch -p1"


def stage_pr(ctx: RunContext) -> int:
    """Write pr/changes.patch and pr/pr.md, measure 'after' coverage on a patched fresh copy.

    Returns:
        Exit code: 0 completed, 1 combined suite failed, 3 stopped at a cap.
    """
    ctx.require("pr")
    passed = [t for t in ctx.state.tests if t.status == Status.PASSED]
    patch_text = build_patch((t.test_file or "", t.code) for t in passed)
    pr_dir = ctx.run_dir / "pr"
    patch_file = workspace.write_file(ctx.run_dir, pr_dir / "changes.patch", patch_text)
    verify = workspace.copy_target(ctx.target, ctx.run_dir, "verify")
    ctx.state.patch_method = _apply_patch(ctx, patch_file, verify, passed) if passed else "nothing to apply"
    result, cov = ctx.executor.run_coverage(verify, ctx.state.package, ctx.config.test_timeout)
    ctx.state.after = summarize_coverage(cov)
    ctx.state.after_suite_ok = result.returncode in (0, 5)
    if not ctx.state.after_suite_ok:
        ctx.state.warnings.append("combined suite failed in verify/:\n" + result.tail(20))
    digest, count = workspace.tree_hash(ctx.target)
    integrity = digest == ctx.state.target_hash
    workspace.write_file(ctx.run_dir, pr_dir / "pr.md", render_pr_md(ctx.state, integrity_ok=integrity, file_count=count))
    ctx.done("pr")
    workspace.verify_untouched(ctx.target, ctx.state.target_hash)
    if not ctx.state.after_suite_ok:
        return 1
    return 3 if ctx.state.stop_reason in ("call_cap", "spend_cap") else 0


def reopen_capped(ctx: RunContext) -> None:
    """On resume, put cap-skipped tests back to where they were, so a higher cap can serve them."""
    for rec in ctx.state.tests:
        if rec.status == Status.SKIPPED and rec.reason in CAP_REASONS and rec.skipped_from:
            rec.status, rec.reason, rec.skipped_from = rec.skipped_from, "", None
    ctx.state.stop_reason = None
    for stage in ("write", "run", "fix", "pr"):
        if stage in ctx.state.stages_done:
            ctx.state.stages_done.remove(stage)
    ctx.save()


def run_all(ctx: RunContext) -> int:
    """Run every stage in order; returns the exit code from stage_pr."""
    stage_gaps(ctx)
    stage_write(ctx)
    stage_run(ctx)
    stage_fix(ctx)
    return stage_pr(ctx)


__all__ = ["RunContext", "create_run", "load_run", "run_all", "reopen_capped", "new_run_id",
           "stage_gaps", "stage_write", "stage_run", "stage_fix", "stage_pr"]
