"""Exception types shared across the engine. The CLI maps each one to an exit code."""


class CtloopError(Exception):
    """Base class for expected, user-facing errors (exit code 1)."""


class TargetModifiedError(CtloopError):
    """The target tree's hash changed during a run, so the run can't be trusted."""


class PathEscapeError(CtloopError):
    """A write, move, or delete would have landed outside the run folder."""


class SandboxError(CtloopError):
    """sandbox-exec is missing or the profile failed to apply (we fail closed)."""


class ProviderError(CtloopError):
    """The model provider failed (missing binary, bad reply, timeout, error result)."""


class BudgetExhausted(CtloopError):
    """A call or spend cap was reached. Stages catch this and stop cleanly."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason  # "call cap" or "spend cap"


class StageOrderError(CtloopError):
    """A stage was run before the stage it depends on."""
