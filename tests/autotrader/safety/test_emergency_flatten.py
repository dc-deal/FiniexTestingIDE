"""
The HARD stop: close what is open, then end — in that order (#356).

Three severities exist and this is the third. A soft block stops new entries and leaves what
is open; HALT (#349) freezes orders and waits for a human; this CLOSES. The distinction is
the whole point: a soft block is the wrong answer to a runaway, because it stops the bot from
making things worse and does nothing about what it already holds.

**The two steps are not decoration.** A close is ASYNCHRONOUS — it is sent on one tick and its
fill arrives on a later one — while `SessionEndSeverity.EMERGENCY` means immediate exit. Doing
both in the same breath would tear the session down before the fills arrived, and the books
would report a flat account while the positions sat at the venue. The session-end validator
already refuses `positions='close'` for exactly this reason
(`session_end_validator.py`, `SessionEndCloseUnsupportedError`).

So: send once, keep draining, end when the book is flat — or say out loud which positions were
never confirmed closed.

Driven against the real `check_emergency_flatten` / `drive_emergency_flatten` bound to a
stub, so the logic under test is the production code.
"""

from types import SimpleNamespace
from typing import List

from python.framework.autotrader.safety.safety_circuit_breaker import SafetyCircuitBreaker
from python.framework.types.autotrader_types.autotrader_config_types import (
    AutoTraderConfig,
    SafetyConfig,
)
from python.framework.types.autotrader_types.operator_alert_types import OperatorAlertKind
from python.framework.types.autotrader_types.safety_session_types import FlattenOutcome
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.decision_event_types import SessionEndSeverity
from tests.shared.operator_alert_recorder import OperatorAlertRecorder


class _RecordingLogger:
    """Captures what reached the operator — a hard stop that says nothing is the defect."""

    def __init__(self):
        self.errors: List[str] = []

    def verbose(self, m, *a, **k): pass
    def debug(self, m, *a, **k): pass
    def info(self, m, *a, **k): pass
    def warning(self, m, *a, **k): pass
    def error(self, m, *a, **k): self.errors.append(m)


class _FlattenStub:
    """
    The attributes the two flatten methods read and write, and nothing else.

    Deliberately narrow, the same discipline as the cold-start write helper: this stub IS the
    documented dependency surface, so a new attribute the path consults has to appear here or
    the test stops describing it.
    """

    def __init__(self, safety: SafetyConfig, trading_model: TradingModel,
                 positions: List[str], close_raises: bool = False):
        self._config = AutoTraderConfig(safety=safety)
        self._trading_model = trading_model
        self._logger = _RecordingLogger()
        self._operator_alerts = OperatorAlertRecorder()   # #235 — the hard stop alerts out of band
        self._flatten_sent_at_tick = None
        self._flatten_reason = ''
        self._flatten_outcome = None
        self._flatten_unconfirmed: List[str] = []
        self._flatten_kept: List[str] = []
        self._safety_blocked = False                       # no soft threshold is configured
        self._on_hard_stop = None                          # #590's seam, unused here
        self.closed: List[str] = []
        self.session_end: List[tuple] = []
        self._open = [SimpleNamespace(position_id=p) for p in positions]
        self._close_raises = close_raises

        stub = self

        class _Portfolio:
            @staticmethod
            def get_open_positions():
                return list(stub._open)

        class _Executor:
            portfolio = _Portfolio()

            @staticmethod
            def close_position(position_id):
                if stub._close_raises:
                    raise RuntimeError('venue unreachable')
                stub.closed.append(position_id)

            @staticmethod
            def request_session_end(reason, severity):
                stub.session_end.append((reason, severity))

        self._executor = _Executor()

    def settle(self) -> None:
        """The fills arrive — the book goes flat, as a later tick would make it."""
        self._open = []

    def check(self, value: float, baseline: float, tick_index: int = 0) -> None:
        SafetyCircuitBreaker.check_emergency_flatten(self, value, baseline, tick_index)

    def _send_emergency_closes(self, reason: str, ticks_processed: int) -> None:
        """The real sender, bound here for the same reason the two entry points are."""
        SafetyCircuitBreaker._send_emergency_closes(self, reason, ticks_processed)

    def drive(self, tick_index: int) -> None:
        SafetyCircuitBreaker.drive_emergency_flatten(self, tick_index)

    def entries_blocked(self) -> bool:
        return SafetyCircuitBreaker.entries_blocked(self)


def _stub(
    trading_model: TradingModel = TradingModel.MARGIN,
    enabled: bool = True,
    flatten: bool = True,
    pct_hard: float = 0.0,
    abs_hard: float = 0.0,
    spot_liquidate: bool = False,
    positions: List[str] = None,
    close_raises: bool = False,
) -> _FlattenStub:
    """Build a stub with the given hard-stop config."""
    return _FlattenStub(
        SafetyConfig(
            enabled=enabled,
            emergency_flatten_enabled=flatten,
            max_drawdown_pct_hard=pct_hard,
            max_drawdown_abs_hard=abs_hard,
            spot_liquidate_to_quote=spot_liquidate,
        ),
        trading_model,
        ['pos_1', 'pos_2'] if positions is None else positions,
        close_raises,
    )


class TestItFiresOnlyWhenItShould:
    """Default OFF, and a soft breach is not a hard one."""

    def test_a_hard_breach_closes_everything(self):
        stub = _stub(pct_hard=20.0)

        stub.check(value=7_000.0, baseline=10_000.0)

        assert stub.closed == ['pos_1', 'pos_2']

    def test_a_loss_inside_the_hard_threshold_closes_nothing(self):
        stub = _stub(pct_hard=20.0)

        stub.check(value=8_500.0, baseline=10_000.0)

        assert stub.closed == []

    def test_the_master_switch_off_closes_nothing(self):
        """Turning it on changes what happens to real money, so off is the default."""
        stub = _stub(flatten=False, pct_hard=1.0)

        stub.check(value=1.0, baseline=10_000.0)

        assert stub.closed == []

    def test_safety_disabled_closes_nothing(self):
        stub = _stub(enabled=False, pct_hard=1.0)

        stub.check(value=1.0, baseline=10_000.0)

        assert stub.closed == []

    def test_the_absolute_threshold_fires_too(self):
        stub = _stub(abs_hard=2_000.0)

        stub.check(value=7_500.0, baseline=10_000.0)

        assert stub.closed == ['pos_1', 'pos_2']

    def test_it_sends_once_not_on_every_tick(self):
        """
        A second round of closes would double-sell what the first round is already closing.

        The fills have not arrived yet — the book still shows the positions — so a naive
        re-check on the next tick sees the same breach and the same open book.
        """
        stub = _stub(pct_hard=20.0)

        stub.check(value=7_000.0, baseline=10_000.0)
        stub.check(value=6_000.0, baseline=10_000.0)
        stub.check(value=5_000.0, baseline=10_000.0)

        assert stub.closed == ['pos_1', 'pos_2']
        assert stub._operator_alerts.kinds == [OperatorAlertKind.EMERGENCY_STOP]   # #235, once

    def test_the_moment_it_fires_reaches_the_latch_seam_once(self):
        """#590 latches the hard stop here, so it is called once, with the reason, at the trip."""
        stub = _stub(pct_hard=20.0)
        fired: List[str] = []
        stub._on_hard_stop = fired.append

        stub.check(value=8_500.0, baseline=10_000.0)
        assert fired == [], 'inside the threshold nothing fires'
        stub.check(value=7_000.0, baseline=10_000.0)
        stub.check(value=6_000.0, baseline=10_000.0)

        assert fired == ['hard drawdown 30.0% > 20.0%']


class TestItBlocksEntriesWithoutASoftThreshold:
    """
    The hard stop is the stronger stop, so it needs no soft threshold to block what the soft one
    blocks. Every soft threshold defaults to off; a profile with only a hard one would otherwise
    let the decision open positions on the very tick the stop fired and through the whole drain,
    and the book would never reach flat.
    """

    def test_nothing_is_blocked_before_it_fires(self):
        stub = _stub(pct_hard=20.0)
        stub.check(value=9_000.0, baseline=10_000.0)
        assert stub.entries_blocked() is False

    def test_once_it_fires_entries_are_blocked(self):
        stub = _stub(pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0, tick_index=100)
        assert stub._safety_blocked is False, 'no soft threshold was configured'
        assert stub.entries_blocked() is True
        stub.drive(101)                                     # still draining
        assert stub.entries_blocked() is True

    def test_spot_holdings_kept_block_entries_too(self):
        """The log line says new entries are blocked — it has to be true."""
        stub = _stub(trading_model=TradingModel.SPOT, pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0)
        assert stub.entries_blocked() is True


class TestTheSessionEndsOnlyAfterTheFillsArrive:
    """The two-step, and the reason it is two steps."""

    def test_it_does_not_end_while_positions_are_still_open(self):
        stub = _stub(pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0, tick_index=100)

        stub.drive(101)

        assert stub.session_end == [], (
            'EMERGENCY means immediate exit — ending here tears the session down before the '
            'fills arrive, and the books then report a flat account that is not flat')

    def test_it_ends_once_the_book_is_flat(self):
        stub = _stub(pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0, tick_index=100)

        stub.settle()
        stub.drive(105)

        assert len(stub.session_end) == 1
        reason, severity = stub.session_end[0]
        assert severity is SessionEndSeverity.EMERGENCY
        assert 'complete' in reason
        assert stub._flatten_outcome is FlattenOutcome.COMPLETE

    def test_it_ends_anyway_at_the_ceiling_and_names_what_is_still_open(self):
        """
        A venue that never answers must not hold the session open for the rest of the month.

        What happens at the ceiling is loud rather than silent: the positions are named, so
        the operator knows exactly what to check by hand.
        """
        stub = _stub(pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0, tick_index=100)

        stub.drive(100 + 1000)

        assert len(stub.session_end) == 1
        reason, severity = stub.session_end[0]
        assert severity is SessionEndSeverity.EMERGENCY
        assert 'incomplete' in reason
        assert any('pos_1' in m and 'pos_2' in m for m in stub._logger.errors)
        assert stub._flatten_outcome is FlattenOutcome.UNCONFIRMED
        assert (stub._flatten_unconfirmed, stub._flatten_kept) == (['pos_1', 'pos_2'], [])
        # #235 — and the operator is told out of band to check the account by hand
        assert stub._operator_alerts.titles[-1] == (
            'EMERGENCY STOP incomplete — check the account by hand')

    def test_it_ends_once_not_on_every_later_tick(self):
        stub = _stub(pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0, tick_index=100)
        stub.settle()

        stub.drive(105)
        stub.drive(106)
        stub.drive(107)

        assert len(stub.session_end) == 1


class TestSpotIsGatedAndTheAsymmetryIsDeliberate:
    """
    A margin position can lose more than the account holds; a spot holding cannot.

    So liquidating a margin position IS the point of a hard stop, while selling a spot
    holding converts an unrealised loss into a realised one — a trading decision rather than
    a safety one. The switch defaults off and the operator turns it on where the account must
    end flat.
    """

    def test_spot_holds_are_not_sold_by_default(self):
        stub = _stub(trading_model=TradingModel.SPOT, pct_hard=20.0)

        stub.check(value=7_000.0, baseline=10_000.0)

        assert stub.closed == [], 'the holding was sold without the switch being on'
        assert any('NOT sold' in m for m in stub._logger.errors), (
            'the operator must hear that the hard stop fired and left the holding standing')

    def test_and_the_session_still_ends(self):
        """The stop is still a stop — it ends the session even when it sells nothing."""
        stub = _stub(trading_model=TradingModel.SPOT, pct_hard=20.0)
        stub.check(value=7_000.0, baseline=10_000.0, tick_index=100)

        stub.drive(101)

        assert len(stub.session_end) == 1
        assert stub.session_end[0][1] is SessionEndSeverity.EMERGENCY
        # #235: one alert — a second one would invite selling by hand, which realises the very
        # loss the switch keeps unrealised
        assert stub._operator_alerts.kinds == [OperatorAlertKind.EMERGENCY_STOP]
        # and the record says KEPT, never "not confirmed" — the same invitation, one layer down
        assert stub._flatten_outcome is FlattenOutcome.HOLDINGS_KEPT
        assert (stub._flatten_kept, stub._flatten_unconfirmed) == (['pos_1', 'pos_2'], [])

    def test_with_the_switch_on_the_holding_is_sold(self):
        stub = _stub(trading_model=TradingModel.SPOT, pct_hard=20.0, spot_liquidate=True)

        stub.check(value=7_000.0, baseline=10_000.0)

        assert stub.closed == ['pos_1', 'pos_2']


class TestACloseThatCannotBeSent:
    """The worst of the three outcomes, and it must not stop the others being tried."""

    def test_every_position_is_attempted_and_the_failure_is_named(self):
        stub = _stub(pct_hard=20.0, close_raises=True)

        stub.check(value=7_000.0, baseline=10_000.0)

        assert stub.closed == []
        for position_id in ('pos_1', 'pos_2'):
            assert any(position_id in m and 'could not send' in m
                       for m in stub._logger.errors), (
                f'{position_id} failed silently — it is still open at the venue')
