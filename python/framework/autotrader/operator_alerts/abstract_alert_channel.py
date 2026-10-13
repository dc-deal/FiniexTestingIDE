"""
FiniexTestingIDE - Abstract Alert Channel

Where operator alerts go (#235). The dispatcher owns the queue, the pacing and the retries; a
channel sends ONE message ONCE and says, by the exception it raises, whether another try could
help — a `ConnectionAttemptFailedError` carrying `terminal`, or an `AlertChannelRateLimitedError`
naming the wait. Anything else it raises is read as our own defect and stops the channel (§43).
"""

from abc import ABC, abstractmethod


class AbstractAlertChannel(ABC):
    """One way to reach the operator."""

    @abstractmethod
    def verify(self) -> None:
        """
        Confirm the channel's credential is accepted, before the session relies on it.

        A refusal raises `ConnectionAttemptFailedError` with `terminal` set; an unreachable
        service raises it without.

        Returns:
            None
        """

    @abstractmethod
    def send(self, text: str) -> None:
        """
        Deliver one message once.

        Args:
            text: The message, already rendered

        Returns:
            None
        """

    @abstractmethod
    def describe(self) -> str:
        """
        Name the channel for a log line — never its secret.

        Returns:
            A short description, e.g. 'telegram (production)'
        """
