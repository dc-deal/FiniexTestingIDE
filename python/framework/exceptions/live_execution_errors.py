"""
FiniexTestingIDE - Live Execution Errors
Exception types for live trading failures (Horizon 2).

Used by LiveTradeExecutor and broker adapters to signal
broker communication and order execution failures.
"""

from python.framework.exceptions.finiex_error import FiniexError


class BrokerConnectionError(FiniexError):
    """
    Broker API unreachable or authentication failed.

    Raised when adapter cannot establish connection to broker
    or when API credentials are invalid/expired.
    """
    pass


class DryRunConflictError(FiniexError):
    """
    A profile tried to enable real orders that the broker's global setting forbids.

    A profile may TIGHTEN the dry-run posture (`true` when the broker default is live)
    and never loosen it. Profiles are copied, shared and edited quickly; enabling real
    money is a deliberate act on the broker's own configuration, not a side effect of
    picking up someone's profile file.
    """
    pass


class OrderTimeoutError(FiniexError):
    """
    Broker did not respond within configured timeout window.

    Raised when an order remains in PENDING state beyond
    TimeoutConfig.order_timeout_seconds.
    """
    pass


class OrderExecutionError(FiniexError):
    """
    Unexpected error during order execution at broker.

    Raised for broker-side errors that don't map to a standard
    RejectionReason (network errors, API format changes, etc.).
    """
    pass
