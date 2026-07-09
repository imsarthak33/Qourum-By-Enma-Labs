"""In-process circuit breaker so a failing provider is skipped, not hammered
(05_AI_ARCHITECTURE §3.5, 06_WORKFLOW §3)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _State:
    consecutive_failures: int = 0
    opened_at: float | None = None


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    cooldown_s: float = 60.0
    _states: dict[str, _State] = field(default_factory=dict)

    def _state(self, name: str) -> _State:
        return self._states.setdefault(name, _State())

    def is_open(self, name: str) -> bool:
        s = self._state(name)
        if s.opened_at is None:
            return False
        if time.monotonic() - s.opened_at >= self.cooldown_s:
            # half-open: allow one probe call through
            s.opened_at = None
            s.consecutive_failures = self.failure_threshold - 1
            return False
        return True

    def record_success(self, name: str) -> None:
        self._states[name] = _State()

    def record_failure(self, name: str) -> None:
        s = self._state(name)
        s.consecutive_failures += 1
        if s.consecutive_failures >= self.failure_threshold:
            s.opened_at = time.monotonic()
