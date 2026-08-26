"""
FiniexTestingIDE - Live Execution Errors
Exception types for live trading failures (Horizon 2).

Used by LiveTradeExecutor and broker adapters to signal
broker communication and order execution failures.
"""

from python.framework.exceptions.finiex_error import FiniexError


class DryRunConflictError(FiniexError):
    """
    A profile tried to enable real orders that the broker's global setting forbids.

    A profile may TIGHTEN the dry-run posture (`true` when the broker default is live)
    and never loosen it. Profiles are copied, shared and edited quickly; enabling real
    money is a deliberate act on the broker's own configuration, not a side effect of
    picking up someone's profile file.
    """
    pass


