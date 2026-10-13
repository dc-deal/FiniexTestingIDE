"""
FiniexTestingIDE - Operator Alert Recorder for Tests

Stands in for a session's operator alerts (#235) where a test builds the tick loop without its
constructor and binds the real methods to a stub: it keeps the kind of every alert sent, so a
test can hold a symptom to being reported once per edge — never once per tick.

Convention: not a pytest fixture — construct it in the test and assign it to the stub.
"""

from typing import List

from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)


class OperatorAlertRecorder:
    """Keeps the kind of every operator alert sent, in order."""

    def __init__(self):
        self.kinds: List[OperatorAlertKind] = []
        self.titles: List[str] = []

    def send(self, kind: OperatorAlertKind, level: OperatorAlertLevel, title: str,
             detail: str = '') -> None:
        self.kinds.append(kind)
        self.titles.append(title)
