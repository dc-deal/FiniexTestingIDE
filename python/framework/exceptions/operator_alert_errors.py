"""
FiniexTestingIDE - Operator Alert Errors

The two ways the out-of-band channels to the operator (#235) can fail: a configuration that
cannot work, refused at startup, and a channel asking us to slow down.
"""

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.exceptions.finiex_error import FiniexError


class OperatorAlertsConfigurationError(FiniexError, ValueError):
    """
    An enabled operator channel that cannot work as configured.

    Refused at startup rather than discovered at the first alert: an unattended session whose
    alert channel was never going to work is the silence this feature exists to end.
    """


class AlertChannelRateLimitedError(ConnectionAttemptFailedError):
    """
    The channel asked us to wait before the next message (HTTP 429).

    Transient by definition — being asked to slow down is an invitation to retry later — and
    it carries how long the other side asked for, so the wait honours it rather than guessing.

    Args:
        message: What was refused, without any secret
        retry_after_s: The wait the channel asked for, 0.0 when it named none
    """

    def __init__(self, message: str, retry_after_s: float):
        super().__init__(message, terminal=False)
        self.retry_after_s = retry_after_s
