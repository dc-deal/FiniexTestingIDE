"""
FiniexTestingIDE - Connection Ladder
One retry ladder and one give-up rule for every external connection (#473).

Seven connections reach outside this process — the signal stream, the producer registry and
its identity probe, the broker's tick socket, its REST endpoint, its warmup bars and its
configuration. Before this unit each decided for itself what to do when the other side did
not answer, and the ones carrying the most consequence decided nothing at all.

What is centralized is the CLASSIFICATION and the arithmetic, not the waiting. The four
callers wait with four different primitives — a stop event, an asyncio sleep, a plain
blocking sleep on the broker worker thread, and in the tick loop no wait at all because a
cadenced caller skips its cycle instead. A ladder that owned the wait would need a plugin
point to serve them; one that owns only the decision needs none.

REPORT RULE: an external call that decides for itself how often to retry, or turns a
transport fault into a domain answer (a rejection, an empty result), bypasses this unit —
flag it. The failure family it exists to prevent is a transient fault reported as a
terminal fact.
"""

import random
import time
from typing import Callable, Optional, Tuple, TypeVar

from python.framework.exceptions.connection_errors import (
    ConnectionAttemptFailedError,
    ConnectionGaveUpError,
    ConnectionInadmissibleError,
)
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import ConnectionOutcome, GiveUpAction

T = TypeVar('T')

# Lower bound of the jitter window. Half the delay is enough to break lockstep between
# clients without letting a retry arrive so early it is indistinguishable from no backoff.
_JITTER_FLOOR = 0.5


class ConnectionLadder:
    """
    The retry decision for one named external connection.

    Args:
        name: The connection as the operator sees it in a log line ('signal_registry',
            'broker_rest'). It is the whole point of a give-up message: which system is
            unreachable, so the reader knows this is not the trading logic failing.
        policy: The ladder's numbers and its give-up rule.
        transient: Exception types worth retrying for this connection.
        terminal: Exception types a retry cannot fix. Checked before `transient`, so an
            overlapping subclass resolves the safe way.
        logger: Session logger — a give-up belongs in the §35 error pot, never only in
            the global log.
    """

    def __init__(
        self,
        name: str,
        policy: ConnectionPolicy,
        logger: AbstractLogger,
        transient: Tuple[type, ...] = (),
        terminal: Tuple[type, ...] = (),
    ):
        self._name = name
        self._policy = policy
        self._logger = logger
        self._transient = transient
        self._terminal = terminal

    def get_name(self) -> str:
        """
        The connection's name as it appears in log lines.

        Returns:
            The name this ladder was constructed with
        """
        return self._name

    def get_policy(self) -> ConnectionPolicy:
        """
        The policy this ladder applies.

        Returns:
            The ConnectionPolicy this ladder was constructed with
        """
        return self._policy

    def classify(self, error: BaseException) -> ConnectionOutcome:
        """
        Decide what one failure means.

        An unlisted exception type is TERMINAL on purpose. A type nobody registered is
        most likely a defect in our own code, and retrying a defect forever costs more
        than stopping does — it also reports the other side's outage for our mistake.

        Args:
            error: The exception the attempt raised

        Returns:
            TRANSIENT, TERMINAL or INADMISSIBLE
        """
        if isinstance(error, ConnectionAttemptFailedError):
            return ConnectionOutcome.TERMINAL if error.terminal else ConnectionOutcome.TRANSIENT
        if isinstance(error, ConnectionInadmissibleError):
            return ConnectionOutcome.INADMISSIBLE
        if self._terminal and isinstance(error, self._terminal):
            return ConnectionOutcome.TERMINAL
        if self._transient and isinstance(error, self._transient):
            return ConnectionOutcome.TRANSIENT
        return ConnectionOutcome.TERMINAL

    def next_delay(self, attempt: int) -> float:
        """
        How long to wait before the next attempt.

        Args:
            attempt: Attempts already made — 1 after the first failure

        Returns:
            Seconds to wait: the initial delay doubled once per previous attempt, capped,
            and spread over [0.5, 1.0) of that value when the policy asks for jitter
        """
        step = max(attempt - 1, 0)
        delay = min(self._policy.initial_delay_s * (2.0 ** step), self._policy.max_delay_s)
        if not self._policy.jitter:
            return delay
        return delay * random.uniform(_JITTER_FLOOR, 1.0)

    def is_exhausted(self, attempt: int) -> bool:
        """
        Whether the attempt budget is used up.

        Args:
            attempt: Attempts already made

        Returns:
            True when the budget is reached. A budget of 0 never exhausts — that is a
            long-lived connection whose whole job is to come back
        """
        if self._policy.attempt_budget <= 0:
            return False
        return attempt >= self._policy.attempt_budget

    def report_attempt(self, attempt: int, error: BaseException, delay_s: float) -> None:
        """
        Say that the ladder is still trying, and when it will try again.

        Args:
            attempt: Attempts already made
            error: What the attempt raised
            delay_s: The wait about to happen
        """
        budget = self._policy.attempt_budget
        of = f'/{budget}' if budget > 0 else ''
        self._logger.warning(
            f'📡 {self._name} unreachable ({error}) — attempt {attempt}{of}, '
            f'retry in {delay_s:.1f}s')

    def give_up(self, attempt: int, error: BaseException, outcome: ConnectionOutcome) -> None:
        """
        Stop trying, say so where the operator will see it, and apply the give-up rule.

        The message names the system rather than the symptom, because the reader's first
        question is whether the trading logic broke. Silence here would be worse than a
        ladder that never ran: "gave up" and "still trying" then look identical.

        Args:
            attempt: Attempts made before stopping
            error: The last failure
            outcome: The classification that ended it

        Raises:
            ConnectionInadmissibleError: the run cannot legitimately continue
            ConnectionGaveUpError: the give-up rule is ABORT
        """
        tries = f'after {attempt} attempt(s)' if attempt > 0 else 'immediately'
        detail = (f'{self._name} is unreachable {tries}: {error}. '
                  f'This is an external system, not the trading logic.')

        if outcome is ConnectionOutcome.INADMISSIBLE:
            self._logger.error(f'❌ {detail}')
            raise ConnectionInadmissibleError(detail)

        action = self._policy.on_give_up
        if action is GiveUpAction.ABORT:
            self._logger.error(f'❌ {detail}')
            raise ConnectionGaveUpError(detail)
        if action is GiveUpAction.DEGRADE:
            self._logger.error(f'⚠️  {detail} Continuing DEGRADED.')
            return
        self._logger.error(f'⚠️  {detail}')


def is_terminal_status(status_code: int) -> bool:
    """
    Whether an HTTP status is worth trying again.

    Transport-library agnostic on purpose — it takes an int, so the `requests` callers,
    the urllib readers and the raw http.client stream all share one answer instead of
    three. 429 is deliberately NOT terminal: being asked to slow down is the clearest
    possible invitation to wait and retry, which is exactly what the ladder does.

    Args:
        status_code: The HTTP status the other side answered with

    Returns:
        True when retrying cannot help — a refusal, a bad request, an unknown route
    """
    if status_code == 429 or status_code >= 500:
        return False
    return status_code >= 400


def run_with_ladder(
    operation: Callable[[], T],
    ladder: ConnectionLadder,
    wait: Optional[Callable[[float], None]] = None,
) -> Optional[T]:
    """
    Run one blocking operation under a ladder until it succeeds or the ladder stops.

    For the plainly-blocking callers only — the boot reads and the broker worker thread.
    The two long-lived loops keep their own, because they do more per pass than retry
    (a cursor to advance, a refill gap to close, terminal frames to honour) and wrapping
    them would hide that.

    Args:
        operation: The call to attempt. Must RAISE on failure; readers that report failure
            as a result raise ConnectionAttemptFailedError at their call site
        ladder: The ladder deciding when to stop
        wait: How to wait between attempts (default time.sleep). Injected by tests so a
            budget can be exercised without spending its delays

    Returns:
        The operation's value, or None when the ladder gave up on a connection whose rule
        is DEGRADE or ALERT

    Raises:
        ConnectionGaveUpError: gave up on a connection whose rule is ABORT
        ConnectionInadmissibleError: the failure means the run must not continue
    """
    sleep = wait if wait is not None else time.sleep
    attempt = 0

    while True:
        try:
            return operation()
        except Exception as error:   # noqa: BLE001 — classified below, never swallowed
            attempt += 1
            outcome = ladder.classify(error)
            if outcome is not ConnectionOutcome.TRANSIENT or ladder.is_exhausted(attempt):
                ladder.give_up(attempt, error, outcome)
                return None
            delay = ladder.next_delay(attempt)
            ladder.report_attempt(attempt, error, delay)
            sleep(delay)
