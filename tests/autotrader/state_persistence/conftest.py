"""
Fixtures for the Algo State Persistence suite (#354).

Code-level fixtures live here (§34): a minimal decision-logic stub exposing only
the persistence hooks the store + pre-flight validator read, a recording logger,
and a store config pointed at a tmp directory.
"""

from typing import Any, Dict, Optional

import pytest

from python.framework.types.config_types.autotrader_defaults_config_types import StatePersistenceDefaults


class StubDecisionLogic:
    """
    Minimal stand-in exposing the persistence hooks (duck-typed).

    Args:
        name: Logic name (used in pre-flight error messages)
        uses: uses_state_persistence() return value
        snapshot: get_state_snapshot() return value
    """

    def __init__(self, name: str = 'stub', uses: bool = True, snapshot: Optional[Dict[str, Any]] = None):
        self.name = name
        self._uses = uses
        self._snapshot = snapshot if snapshot is not None else {}

    def uses_state_persistence(self) -> bool:
        return self._uses

    def get_state_snapshot(self) -> Dict[str, Any]:
        return self._snapshot


class RecordingLogger:
    """Captures log calls so tests can assert on emitted warnings/infos."""

    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def debug(self, msg: str) -> None:
        pass


@pytest.fixture
def make_stub_logic():
    """Factory for StubDecisionLogic (name / uses / snapshot)."""
    def _make(name: str = 'stub', uses: bool = True, snapshot: Optional[Dict[str, Any]] = None):
        return StubDecisionLogic(name=name, uses=uses, snapshot=snapshot)
    return _make


@pytest.fixture
def logger() -> RecordingLogger:
    return RecordingLogger()


@pytest.fixture
def store_config(tmp_path) -> StatePersistenceDefaults:
    """A store config pointed at an isolated tmp directory (fast, no real I/O dir)."""
    return StatePersistenceDefaults(
        enabled=True,
        path=str(tmp_path / 'session_state'),
        save_interval_ticks=10,
        save_interval_seconds=3600.0,
        max_age_trading_days=5,
        on_corrupt='warn_reset',
        on_stale='warn_reset',
    )
