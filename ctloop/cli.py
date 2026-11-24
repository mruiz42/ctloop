"""Thin argparse CLI over the engine. Exit codes: 0 done, 1 error, 2 usage, 3 stopped at a cap."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ctloop import stages, workspace
from ctloop.config import PROVIDERS, RunConfig
from ctloop.errors import CtloopError

log = logging.getLogger("ctloop")


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return value


def _positive_float(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return value


def _shared(p: argparse.ArgumentParser) -> None:
    # Defaults are None so resume can tell "given explicitly" from "not given" (D9).
    p.add_argument("--runs-dir", type=Path, default=Path("runs"))
    p.add_argument("--provider", choices=PROVIDERS)
    p.add_argument("--stub-dir")
    p.add_argument("--model")
    p.add_argument("--max-calls", type=_positive_int)
    p.add_argument("--max-fix-attempts", type=_positive_int)
    p.add_argument("--call-timeout", type=_positive_float)
    p.add_argument("--test-timeout", type=_positive_float)
    p.add_argument("--max-budget-usd", type=_positive_float, help="per claude -p call")
    p.add_argument("--max-run-usd", type=_positive_float, help="per run")
    p.add_argument("--unsafe-no-sandbox", action="store_true", default=None,
                   help="run generated tests WITHOUT sandbox-exec (not recommended)")
    p.add_argument("--log-level", default="INFO")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser with all subcommands."""
    parser = argparse.ArgumentParser(prog="ctloop", description="Converging test loop")
    sub = parser.add_subparsers(dest="command", required=True)
    g = sub.add_parser("gaps", help="create a run: copy target, baseline coverage, list gaps")
    g.add_argument("--target", type=Path, required=True)
    g.add_argument("--package")
    _shared(g)
    for name in ("write", "run", "fix", "pr"):
        s = sub.add_parser(name, help=f"run the {name} stage on an existing run")
        s.add_argument("--run", required=True, dest="run_id")
        _shared(s)
    a = sub.add_parser("run-all", help="run every stage (new run, or --resume RUN_ID)")
    src = a.add_mutually_exclusive_group(required=True)
    src.add_argument("--target", type=Path)
    src.add_argument("--resume", dest="run_id")
    a.add_argument("--package")
    _shared(a)
    return parser


def _overrides(args: argparse.Namespace) -> dict:
    keys = ("provider", "stub_dir", "model", "max_calls", "max_fix_attempts", "call_timeout",
            "test_timeout", "max_budget_usd", "max_run_usd", "unsafe_no_sandbox")
    out = {k: getattr(args, k) for k in keys}
    if out["stub_dir"]:
        out["stub_dir"] = str(Path(out["stub_dir"]).resolve())
    return out


def _attach_run_log(run_dir: Path) -> None:
    handler = logging.FileHandler(workspace.safe_path(run_dir, run_dir / "run.log"))
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


def _print_gaps(ctx: stages.RunContext) -> None:
    st = ctx.state
    print(f"RUN_ID {st.run_id}")
    print(f"baseline coverage: {st.baseline['percent'] if st.baseline else 'n/a'}%")
    for t in st.tests:
        print(f"  gap  {t.module}.{t.qualname}  missing lines {t.missing_lines}")
    for u in st.untestable:
        print(f"  skip {u['file']}::{u['qualname']}  ({u['reason']})")


def _print_summary(ctx: stages.RunContext, code: int) -> None:
    st = ctx.state
    counts: dict[str, int] = {}
    for t in st.tests:
        counts[str(t.status)] = counts.get(str(t.status), 0) + 1
    before = st.baseline["percent"] if st.baseline else "n/a"
    after = st.after["percent"] if st.after else "n/a"
    print(f"RUN_ID {st.run_id}")
    print(f"coverage {before}% -> {after}%   tests {counts}   calls {st.calls_used}/{ctx.config.max_calls}"
          f"   spend ${st.cost_usd:.4f}   stop={st.stop_reason or 'completed'}   exit={code}")
    print(f"PR folder: {ctx.run_dir / 'pr'}")


def _dispatch(args: argparse.Namespace) -> int:
    overrides = _overrides(args)
    if args.command == "gaps" or (args.command == "run-all" and args.target):
        config = RunConfig().merged(overrides)
        ctx = stages.create_run(args.target, args.package, args.runs_dir, config)
    else:
        ctx = stages.load_run(args.runs_dir, args.run_id, overrides)
        if args.command == "run-all":
            stages.reopen_capped(ctx)
    _attach_run_log(ctx.run_dir)
    code = 0
    try:
        if args.command == "gaps":
            stages.stage_gaps(ctx)
            _print_gaps(ctx)
        elif args.command == "write":
            stages.stage_write(ctx)
        elif args.command == "run":
            stages.stage_run(ctx)
        elif args.command == "fix":
            stages.stage_fix(ctx)
        elif args.command == "pr":
            code = stages.stage_pr(ctx)
            _print_summary(ctx, code)
        else:
            code = stages.run_all(ctx)
            _print_summary(ctx, code)
    finally:
        # Every command ends by proving the target is unchanged (raises -> exit 1).
        workspace.verify_untouched(ctx.target, ctx.state.target_hash)
    if args.command in ("write", "run", "fix"):
        print(f"RUN_ID {ctx.state.run_id}: {args.command} done, calls {ctx.state.calls_used}")
        if ctx.state.stop_reason:
            code = 3
    return code


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s", force=True)
    try:
        return _dispatch(args)
    except CtloopError as exc:
        log.error("%s", exc)
        return 1
    except ValueError as exc:
        log.error("invalid configuration: %s", exc)
        return 1
