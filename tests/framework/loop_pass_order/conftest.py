"""
FiniexTestingIDE - Loop Pass Order Fixtures

The step recorder both pass-order suites write into: a spy patched onto a collaborator's CLASS
appends a short step name and calls the real method, so the run behaves as it would without it.
"""

from typing import Any, Callable, List, Optional
from unittest.mock import MagicMock

import pytest

from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.types.decision_logic_types import Decision


class PassSteps:
    """
    The steps one run took, in the order it took them.

    Args:
        monkeypatch: The test's patcher, which undoes every spy after the test
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.names: List[str] = []

    def spy(self, owner: type, method: str, step: str) -> None:
        """
        Record a step each time a method of a class is called, then call it.

        Args:
            owner: The class whose instances the run uses
            method: The method's name
            step: The step name recorded
        """
        real = getattr(owner, method)

        def recorded(instance: Any, *args: Any, **kwargs: Any) -> Any:
            self.names.append(step)
            return real(instance, *args, **kwargs)

        self._monkeypatch.setattr(owner, method, recorded)

    def record(self, step: str) -> None:
        """
        Record a step a test double took.

        Args:
            step: The step name recorded
        """
        self.names.append(step)

    def orchestrator(self, ghost_decisions: Optional[List[Optional[Decision]]] = None
                     ) -> Callable[[DeterministicProbe], MagicMock]:
        """
        An orchestrator factory for the harness, recording the decision compute of a tick
        ('algo') and of a heartbeat ('ghost').

        Args:
            ghost_decisions: What the heartbeats compute, in order; None once the list is
                used up, and always None when no list is given

        Returns:
            The factory, taking the probe the orchestrator delegates to
        """
        planned = list(ghost_decisions or [])

        def build(logic: DeterministicProbe) -> MagicMock:
            orchestrator = MagicMock()

            def process_tick(tick: Any, current_bars: Any, bar_history: Any,
                             bar_render_state: Any = None) -> Decision:
                self.names.append('algo')
                return logic.compute_tick(tick, {})

            def process_heartbeat() -> Optional[Decision]:
                self.names.append('ghost')
                return planned.pop(0) if planned else None

            orchestrator.process_tick.side_effect = process_tick
            orchestrator.process_heartbeat.side_effect = process_heartbeat
            return orchestrator

        return build

    def frames(self) -> MagicMock:
        """
        A state-frame recorder that records each pass it is handed ('frame').

        Returns:
            The recorder double
        """
        recorder = MagicMock()
        recorder.on_pass.side_effect = lambda *args, **kwargs: self.names.append('frame')
        return recorder


@pytest.fixture
def steps(monkeypatch: pytest.MonkeyPatch) -> PassSteps:
    """
    A fresh step recorder whose spies are undone after the test.

    Args:
        monkeypatch: pytest's patcher

    Returns:
        The recorder
    """
    return PassSteps(monkeypatch)
