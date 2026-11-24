"""Run configuration: caps, timeouts, and provider settings, with merge-on-resume.

The config is stored in state.json when a run is created. On resume, any flag the
user gave explicitly overrides the stored value (D9), so a capped run can continue
with a higher cap.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any

PROVIDERS = ("claude", "stub")


@dataclass
class RunConfig:
    """All tunables for a run. Defaults match the brief's caps."""

    provider: str = "claude"
    stub_dir: str | None = None
    model: str = "sonnet"
    max_calls: int = 30
    max_fix_attempts: int = 3
    call_timeout: float = 120.0
    test_timeout: float = 60.0
    max_budget_usd: float = 0.25  # per claude -p call
    max_run_usd: float = 2.00  # per run, tracked from total_cost_usd
    unsafe_no_sandbox: bool = False

    def validate(self) -> None:
        """Raise ValueError with a clear message if any setting is out of range."""
        if self.provider not in PROVIDERS:
            raise ValueError(f"provider must be one of {PROVIDERS}, got {self.provider!r}")
        if self.provider == "stub" and not self.stub_dir:
            raise ValueError("--provider stub requires --stub-dir")
        for name in ("max_calls", "max_fix_attempts"):
            value = getattr(self, name)
            if not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        for name in ("call_timeout", "test_timeout", "max_budget_usd", "max_run_usd"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive number")

    def to_dict(self) -> dict[str, Any]:
        """Serialize for state.json."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunConfig:
        """Build from state.json, ignoring unknown keys from older versions."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def merged(self, overrides: dict[str, Any]) -> RunConfig:
        """Return a copy with every non-None override applied."""
        data = self.to_dict()
        data.update({k: v for k, v in overrides.items() if v is not None and k in data})
        return RunConfig.from_dict(data)
