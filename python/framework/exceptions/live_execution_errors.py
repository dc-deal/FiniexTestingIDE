# ============================================
# python/framework/exceptions/live_execution_errors.py
# ============================================
"""
FiniexTestingIDE - Live Execution Errors
Exception types for live trading failures (Horizon 2).

Used by LiveTradeExecutor and broker adapters to signal
broker communication and order execution failures.
"""


class BrokerConnectionError(Exception):
    """
    Broker API unreachable or authentication failed.

    Raised when adapter cannot establish connection to broker
    or when API credentials are invalid/expired.
    """
    pass


class OrderTimeoutError(Exception):
    """
    Broker did not respond within configured timeout window.

    Raised when an order remains in PENDING state beyond
    TimeoutConfig.order_timeout_seconds.
    """
    pass


class OrderExecutionError(Exception):
    """
    Unexpected error during order execution at broker.

    Raised for broker-side errors that don't map to a standard
    RejectionReason (network errors, API format changes, etc.).
    """
    pass
