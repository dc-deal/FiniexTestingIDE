"""
Test doubles for the operator alert suite (#235): a channel that answers from a script and a
monitoring service that keeps every URL it was asked. Shared by the unit and the session tests,
so both hold the code to the same stand-ins.
"""

import threading
from types import SimpleNamespace
from typing import List, Optional

from python.framework.autotrader.operator_alerts.abstract_alert_channel import (
    AbstractAlertChannel,
)

PING_URL = 'https://hc-ping.example/9b7f-secret-uuid'
FAIL_URL = 'https://hc-ping.example/9b7f-secret-uuid/fail'


class ScriptedChannel(AbstractAlertChannel):
    """
    A channel answering each attempt from a script: None delivers, an exception fails.

    Args:
        outcomes: One entry per attempt, in order; delivery once the script runs out
        gate: When given, every attempt waits for it — a channel that hangs
        verify_error: Raised by verify(), None to accept
    """

    def __init__(self, outcomes: Optional[List[Optional[Exception]]] = None,
                 gate: Optional[threading.Event] = None,
                 verify_error: Optional[Exception] = None):
        self.delivered: List[str] = []
        self.attempts = 0
        self.verified = 0
        self._outcomes = list(outcomes or [])
        self._gate = gate
        self._verify_error = verify_error

    def verify(self) -> None:
        self.verified += 1
        if self._verify_error is not None:
            raise self._verify_error

    def send(self, text: str) -> None:
        self.attempts += 1
        if self._gate is not None:
            self._gate.wait()
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if outcome is not None:
            raise outcome
        self.delivered.append(text)

    def describe(self) -> str:
        return 'scripted'

    def titles(self) -> List[str]:
        """The first line of every delivered message."""
        return [text.splitlines()[0] for text in self.delivered]


class Monitor:
    """
    A monitoring service answering every request with one status, keeping the URLs.

    Args:
        status: The HTTP status every request is answered with
    """

    def __init__(self, status: int = 200):
        self.status = status
        self.requests: List[str] = []
        self.fail_with: Optional[Exception] = None
        self._lock = threading.Lock()

    def get(self, url, timeout):
        if self.fail_with is not None:
            raise self.fail_with
        with self._lock:
            self.requests.append(url)
        return SimpleNamespace(status_code=self.status)
