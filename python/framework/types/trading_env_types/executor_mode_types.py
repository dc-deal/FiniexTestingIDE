"""
FiniexTestingIDE - Executor Mode Types

Which of the two pipelines an executor runs in. A type rather than a detail of the executor:
the order-event vocabulary declares, member by member, which pipeline emits it.
"""

from enum import Enum


class ExecutorMode(Enum):
    """Execution mode for trade executors."""
    SIMULATION = 'simulation'
    LIVE = 'live'
