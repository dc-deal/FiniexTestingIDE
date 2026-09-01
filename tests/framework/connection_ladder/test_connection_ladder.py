"""
FiniexTestingIDE - Connection Ladder Tests (#473)

The shared retry decision every external connection routes through: how a failure is
classified, how the delay grows, when the budget runs out, and what a give-up does.

Nothing here sleeps — `run_with_ladder` takes an injected waiter, so a three-attempt
budget with a 60 s cap costs no wall time.
"""

from typing import List, Tuple

import pytest

from python.framework.exceptions.connection_errors import (
    ConnectionAttemptFailedError,
    ConnectionGaveUpError,
    ConnectionInadmissibleError,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.connection_types import ConnectionOutcome, GiveUpAction
from python.framework.utils.connection_ladder import ConnectionLadder, run_with_ladder


class RecordingLogger:
    """Minimal AbstractLogger stand-in that keeps what was said, at which level."""

    def __init__(self):
        self.lines: List[Tuple[str, str]] = []

    def verbose(self, message: str) -> None:
        self.lines.append(('verbose', message))

    def debug(self, message: str) -> None:
        self.lines.append(('debug', message))

    def info(self, message: str) -> None:
        self.lines.append(('info', message))

    def warning(self, message: str) -> None:
        self.lines.append(('warning', message))

    def error(self, message: str) -> None:
        self.lines.append(('error', message))

    def levels(self) -> List[str]:
        return [level for level, _ in self.lines]

    def text(self) -> str:
        return '\n'.join(message for _, message in self.lines)


class TransientFault(Exception):
    """Stand-in for a dropped socket / 5xx."""


class RefusedCredential(Exception):
    """Stand-in for a rejected token."""


def build_ladder(logger: RecordingLogger, **policy_overrides) -> ConnectionLadder:
    """
    A ladder over the two stand-in exception types, jitter off unless asked.

    Args:
        logger: The recording logger to attach
        **policy_overrides: Fields to override on the default ConnectionPolicy

    Returns:
        A ConnectionLadder named 'test_connection'
    """
    defaults = {'initial_delay_s': 1.0, 'max_delay_s': 8.0, 'jitter': False}
    defaults.update(policy_overrides)
    return ConnectionLadder(
        name='test_connection',
        policy=ConnectionPolicy(**defaults),
        logger=logger,
        transient=(TransientFault,),
        terminal=(RefusedCredential,),
    )


class TestClassify:
    """classify(error) — which of the three outcomes a failure means."""

    def test_registered_transient_type(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.classify(TransientFault('502')) is ConnectionOutcome.TRANSIENT

    def test_registered_terminal_type(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.classify(RefusedCredential('401')) is ConnectionOutcome.TERMINAL

    def test_unregistered_type_is_terminal(self):
        # An exception nobody registered is most likely our own defect. Retrying a defect
        # forever reports their outage for our mistake.
        ladder = build_ladder(RecordingLogger())
        assert ladder.classify(ValueError('typo')) is ConnectionOutcome.TERMINAL

    def test_attempt_failed_carries_its_own_verdict(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.classify(
            ConnectionAttemptFailedError('no answer')) is ConnectionOutcome.TRANSIENT
        assert ladder.classify(
            ConnectionAttemptFailedError('bad token', terminal=True)) is ConnectionOutcome.TERMINAL

    def test_inadmissible_passes_through(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.classify(
            ConnectionInadmissibleError('no bars')) is ConnectionOutcome.INADMISSIBLE

    def test_terminal_wins_over_transient_on_overlap(self):
        # A subclass registered on both sides resolves the safe way.
        class Both(TransientFault, RefusedCredential):
            pass

        ladder = build_ladder(RecordingLogger())
        assert ladder.classify(Both()) is ConnectionOutcome.TERMINAL


class TestNextDelay:
    """next_delay(attempt) — exponential growth, capped, optionally jittered."""

    def test_first_retry_uses_the_initial_delay(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.next_delay(1) == pytest.approx(1.0)

    def test_doubles_per_attempt(self):
        ladder = build_ladder(RecordingLogger())
        assert [ladder.next_delay(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]

    def test_capped_at_max(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.next_delay(9) == pytest.approx(8.0)

    def test_attempt_zero_does_not_go_below_initial(self):
        ladder = build_ladder(RecordingLogger())
        assert ladder.next_delay(0) == pytest.approx(1.0)

    def test_jitter_stays_within_the_half_window(self):
        ladder = build_ladder(RecordingLogger(), jitter=True)
        samples = [ladder.next_delay(3) for _ in range(200)]
        assert all(2.0 <= s < 4.0 for s in samples)
        # Not a constant — the whole point is that clients do not return in lockstep.
        assert len(set(samples)) > 1


class TestBudget:
    """is_exhausted(attempt) — when the ladder stops trying."""

    def test_zero_budget_never_exhausts(self):
        ladder = build_ladder(RecordingLogger(), attempt_budget=0)
        assert ladder.is_exhausted(1_000) is False

    def test_exhausts_at_the_budget(self):
        ladder = build_ladder(RecordingLogger(), attempt_budget=3)
        assert [ladder.is_exhausted(n) for n in (1, 2, 3)] == [False, False, True]


class TestGiveUp:
    """give_up() — the give-up rule, and that it is never silent."""

    def test_abort_raises_and_names_the_system(self):
        logger = RecordingLogger()
        ladder = build_ladder(logger, on_give_up=GiveUpAction.ABORT)
        with pytest.raises(ConnectionGaveUpError):
            ladder.give_up(3, TransientFault('502'), ConnectionOutcome.TRANSIENT)
        assert 'test_connection' in logger.text()
        assert 'not the trading logic' in logger.text()

    def test_degrade_returns_and_says_so(self):
        logger = RecordingLogger()
        ladder = build_ladder(logger, on_give_up=GiveUpAction.DEGRADE)
        ladder.give_up(3, TransientFault('502'), ConnectionOutcome.TRANSIENT)
        assert 'DEGRADED' in logger.text()

    def test_inadmissible_raises_whatever_the_rule_says(self):
        logger = RecordingLogger()
        ladder = build_ladder(logger, on_give_up=GiveUpAction.DEGRADE)
        with pytest.raises(ConnectionInadmissibleError):
            ladder.give_up(1, ConnectionInadmissibleError('0/200 bars'),
                           ConnectionOutcome.INADMISSIBLE)

    def test_give_up_always_reaches_the_error_pot(self):
        # §35: a give-up that only whispers is worse than a ladder that never ran —
        # "gave up" then looks exactly like "still trying".
        for action in (GiveUpAction.ABORT, GiveUpAction.DEGRADE, GiveUpAction.ALERT):
            logger = RecordingLogger()
            ladder = build_ladder(logger, on_give_up=action)
            try:
                ladder.give_up(2, TransientFault('502'), ConnectionOutcome.TRANSIENT)
            except ConnectionGaveUpError:
                pass
            assert 'error' in logger.levels(), f'{action} did not reach the pot'


class TestRunWithLadder:
    """run_with_ladder() — the blocking-caller convenience."""

    def test_returns_the_value_on_first_success(self):
        ladder = build_ladder(RecordingLogger())
        waits = []
        assert run_with_ladder(lambda: 'ok', ladder, wait=waits.append) == 'ok'
        assert waits == []

    def test_succeeds_on_the_third_attempt(self):
        ladder = build_ladder(RecordingLogger(), attempt_budget=5)
        waits = []
        attempts = {'n': 0}

        def flaky():
            attempts['n'] += 1
            if attempts['n'] < 3:
                raise TransientFault('proxy cycling')
            return 'registry'

        assert run_with_ladder(flaky, ladder, wait=waits.append) == 'registry'
        assert attempts['n'] == 3
        assert waits == [1.0, 2.0]

    def test_budget_exhausted_degrades_to_none(self):
        logger = RecordingLogger()
        ladder = build_ladder(logger, attempt_budget=3, on_give_up=GiveUpAction.DEGRADE)
        waits = []

        def always_down():
            raise TransientFault('connection refused')

        assert run_with_ladder(always_down, ladder, wait=waits.append) is None
        assert len(waits) == 2   # waited after attempts 1 and 2, gave up on 3
        assert 'DEGRADED' in logger.text()

    def test_terminal_stops_immediately_without_waiting(self):
        logger = RecordingLogger()
        ladder = build_ladder(logger, attempt_budget=5, on_give_up=GiveUpAction.ALERT)
        waits = []

        def refused():
            raise RefusedCredential('401')

        assert run_with_ladder(refused, ladder, wait=waits.append) is None
        assert waits == []

    def test_abort_propagates(self):
        ladder = build_ladder(RecordingLogger(), attempt_budget=2,
                              on_give_up=GiveUpAction.ABORT)
        with pytest.raises(ConnectionGaveUpError):
            run_with_ladder(lambda: (_ for _ in ()).throw(TransientFault('down')),
                            ladder, wait=lambda _: None)

    def test_every_retry_is_announced(self):
        logger = RecordingLogger()
        ladder = build_ladder(logger, attempt_budget=3, on_give_up=GiveUpAction.DEGRADE)

        def always_down():
            raise TransientFault('502')

        run_with_ladder(always_down, ladder, wait=lambda _: None)
        assert logger.levels().count('warning') == 2
        assert 'attempt 1/3' in logger.text()
        assert 'attempt 2/3' in logger.text()
