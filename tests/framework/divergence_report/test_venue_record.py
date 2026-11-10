"""
What the venue's own record says in the divergence report (#512): the trades it published inside
a run of ids our archive lacks, the trades of a decision bar a capture difference is proposed for,
and the movements of the account its ledger shows that are no order of the bot — which end the
compared window where the ledger was read.
"""

from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import pytest

from python.framework.exceptions.connection_errors import ConnectionGaveUpError
from python.framework.reporting.builders.divergence.live_segment_builder import derive_segments
from python.framework.reporting.builders.divergence.live_trading_day_builder import closing_of
from python.framework.reporting.builders.divergence.venue_record_builder import (
    account_changes_of,
    bot_order_refs,
    capture_bars,
    confirm_capture,
    decision_bar_of,
    trades_inside_run,
)
from python.framework.reporting.builders.divergence.venue_side_collector import (
    VenueSideCollector,
)
from python.framework.types.api.divergence_report_types import (
    DayDecision,
    DecisionComparison,
    DecisionComparisonState,
    DivergenceGap,
    ExplainedFlipKind,
    GapSource,
    VenueAccountChange,
    WindowEndCause,
)
from python.framework.types.api.report_types import OrderEventRow
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.framework.types.venue_history_types import (
    VenueLedgerEntry,
    VenueLedgerEntryKind,
    VenueTrade,
)
from python.framework.validators.divergence_day_validator import live_side_of
from python.framework.venue_history.abstract_venue_ledger_reader import (
    AbstractVenueLedgerReader,
)
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)
from tests.shared.divergence_live_chain import (
    CRYPTO_ANCHOR,
    FIRST_DAY,
    FIRST_TICK,
    days_from,
    deployment,
    records_of,
    session,
)

_T0 = datetime(2026, 11, 25, 9, tzinfo=timezone.utc)


def _at(minutes: float) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _trade(trade_id: int, minutes: float) -> VenueTrade:
    return VenueTrade(trade_id=trade_id, executed_at=_at(minutes), price=3000.0, volume=0.01)


def _run(before: int = 100, after: int = 140) -> DivergenceGap:
    return DivergenceGap(source=GapSource.TRADE_ID_RUN, start=_at(0), end=_at(5), confirmed=False,
                         trade_id_before=before, trade_id_after=after)


def _entry(kind: VenueLedgerEntryKind, at: datetime, asset: str = 'USD', amount: float = -150.0,
           fee: float = 0.0, order_ref: Optional[str] = None) -> VenueLedgerEntry:
    return VenueLedgerEntry(entry_id=f'L-{at:%H%M%S}-{asset}', venue_ref='T-1', executed_at=at,
                       kind=kind, venue_type=kind.value, venue_subtype='', asset=asset,
                       amount=amount, fee=fee, order_ref=order_ref)


def _capture_day(bar_open: Optional[datetime], kind=ExplainedFlipKind.CAPTURE,
                 state=DecisionComparisonState.ONE_SIDED) -> DecisionComparison:
    return DecisionComparison(
        trading_day=date(2026, 11, 25), closing=_at(1440), state=state,
        live=DayDecision(frame_time=_at(1), decision_bar_open=bar_open),
        backtest=DayDecision(frame_time=_at(1), decision_bar_open=bar_open),
        proposed_kind=kind,
        detail="2026-11-25 09:01 · workers differ: rsi 61.2 / 58.9 · unconfirmed until the "
               "venue's trades show it")


class TestARunOfMissingIds:

    def test_counts_the_venues_trades_strictly_between_its_edges(self):
        trades = [_trade(100, 0), _trade(101, 1), _trade(139, 4), _trade(140, 5)]
        assert trades_inside_run(_run(), trades) == 2

    def test_ids_the_venue_never_published_leave_nothing(self):
        assert trades_inside_run(_run(), [_trade(100, 0), _trade(140, 5)]) == 0

    def test_a_run_without_its_ids_is_refused(self):
        with pytest.raises(ValueError, match='names no archive trade ids'):
            trades_inside_run(_run().model_copy(update={'trade_id_before': None}), [])


class TestACaptureDifference:

    def test_the_decision_bar_is_the_one_that_closed_in_the_pass(self):
        assert decision_bar_of(_capture_day(_at(-60)), 'H1') == (_at(-60), _at(0))
        assert decision_bar_of(_capture_day(None), 'H1') is None

    def test_only_one_sided_capture_days_are_fetched(self):
        days = [_capture_day(_at(-60)),
                _capture_day(_at(-120), kind=ExplainedFlipKind.ACCOUNT_VALUE),
                _capture_day(_at(-180), state=DecisionComparisonState.AGREE),
                _capture_day(None)]
        assert capture_bars(days, 'H1') == [(_at(-60), _at(0))]

    def test_trades_the_archive_lacks_confirm_it(self):
        confirmed = confirm_capture(_capture_day(_at(-60)), {1, 2},
                                    [_trade(1, -50), _trade(2, -40), _trade(3, -30)])
        assert confirmed.capture_confirmed and confirmed.capture_missing_trades == 1
        assert "confirmed: the archive lacks 1 of the venue's 3 trades" in confirmed.detail
        assert 'unconfirmed until' not in confirmed.detail

    def test_an_archive_holding_every_trade_refutes_it(self):
        refuted = confirm_capture(_capture_day(_at(-60)), {1, 2}, [_trade(1, -50), _trade(2, -40)])
        assert refuted.capture_confirmed is False and refuted.capture_missing_trades == 0
        assert ("not confirmed: the archive holds every one of the venue's 2 trades"
                in refuted.detail)


class TestTheAccountsMovements:

    _BOT = {'O-BOT-1'}

    def test_the_bots_trade_moves_nothing_and_every_other_movement_counts(self):
        entries = [
            _entry(VenueLedgerEntryKind.TRADE, _at(1), order_ref='O-BOT-1'),
            _entry(VenueLedgerEntryKind.TRADE, _at(1), asset='ETH', amount=0.05,
                   order_ref='O-BOT-1'),
            _entry(VenueLedgerEntryKind.DEPOSIT, _at(2), amount=500.0),
            _entry(VenueLedgerEntryKind.TRADE, _at(3), order_ref='O-BY-HAND'),
            _entry(VenueLedgerEntryKind.TRADE, _at(4), order_ref=None),
            _entry(VenueLedgerEntryKind.CONVERSION, _at(5), amount=826.84, fee=8.19),
        ]
        changes = account_changes_of(entries, self._BOT, {'ETH', 'USD'})
        assert [change.kind for change in changes] == [
            VenueLedgerEntryKind.DEPOSIT, VenueLedgerEntryKind.TRADE, VenueLedgerEntryKind.TRADE,
            VenueLedgerEntryKind.CONVERSION]
        assert 'order O-BY-HAND, which no order event of the bot names' in changes[1].detail
        assert 'named no order' in changes[2].detail
        assert changes[3].balance_change == pytest.approx(818.65)

    def test_a_currency_the_account_does_not_compare_and_a_zero_move_count_nothing(self):
        entries = [_entry(VenueLedgerEntryKind.EARN, _at(1), asset='DOT', amount=0.3),
                   _entry(VenueLedgerEntryKind.ADJUSTMENT, _at(2), amount=0.0)]
        assert account_changes_of(entries, self._BOT, {'ETH', 'USD'}) == []

    def test_the_bots_orders_are_every_handle_its_events_carry(self):
        events = [OrderEventRow(scenario_name='my_session', seq=1,
                                event_type=OrderEventType.SUBMITTED, order_id='o1',
                                broker_ref='O-1'),
                  OrderEventRow(scenario_name='my_session', seq=2,
                                event_type=OrderEventType.MODIFIED, order_id='o1',
                                broker_ref='O-2', previous_broker_ref='O-1'),
                  OrderEventRow(scenario_name='my_session', seq=3,
                                event_type=OrderEventType.REJECTED, order_id='o2')]
        assert bot_order_refs(events) == {'O-1', 'O-2'}


def _restart_deployment(first_lots: float, last_lots: float):
    """Two sessions, the second started an hour after the first stopped on day 3."""
    stopped_at = FIRST_TICK.replace(minute=0) + timedelta(days=3, hours=10)
    first = session('s1', FIRST_TICK, days_from(FIRST_DAY, 3), ended_at=stopped_at, closed=True,
                    last_lots=last_lots)
    second = session('s2', stopped_at + timedelta(hours=1),
                     days_from(FIRST_DAY + timedelta(days=3), 4), first_lots=first_lots,
                     first_period_no=5)
    return deployment(first, second)


def _change(at: datetime) -> VenueAccountChange:
    return VenueAccountChange(at=at, kind=VenueLedgerEntryKind.DEPOSIT, asset='USD',
                              balance_change=500.0, venue_ref='Q-1',
                              detail='deposit (deposit) of +500 USD')


class TestTheLedgerDecidesTheWindow:

    def test_a_movement_ends_it_at_the_last_boundary_before(self):
        capture = _restart_deployment(0.0, 0.0)
        moved = FIRST_TICK + timedelta(days=4, hours=6)
        segment = derive_segments(capture, venue_changes=[_change(moved)])[0][0]
        assert segment.window.end_cause is WindowEndCause.VENUE_ACCOUNT_CHANGE
        assert segment.window.end == closing_of(FIRST_DAY + timedelta(days=3), CRYPTO_ANCHOR)
        assert 'deposit (deposit) of +500 USD' in segment.window.end_detail
        assert segment.venue_account_changes == [_change(moved)]

    def test_a_ledger_without_movements_overrides_the_frames(self):
        # The frames show another position after the restart; the venue's ledger, read, shows
        # nothing but the bot's orders — it decides
        capture = _restart_deployment(0.0, 0.5)
        assert (derive_segments(capture)[0][0].window.end_cause
                is WindowEndCause.UNDECLARED_ACCOUNT_CHANGE)
        window = derive_segments(capture, venue_changes=[])[0][0].window
        assert window.end_cause is not WindowEndCause.UNDECLARED_ACCOUNT_CHANGE

    def test_a_movement_before_the_window_starts_is_not_counted(self):
        capture = _restart_deployment(0.0, 0.0)
        before = FIRST_TICK - timedelta(days=1)
        window = derive_segments(capture, venue_changes=[_change(before)])[0][0].window
        assert window.end_cause is not WindowEndCause.VENUE_ACCOUNT_CHANGE

    def test_the_live_side_carries_the_movements(self):
        capture = _restart_deployment(0.0, 0.0)
        moved = FIRST_TICK + timedelta(days=4, hours=6)
        segments, named = derive_segments(capture, venue_changes=[_change(moved)])
        records = {**records_of(capture.sessions[0]), **records_of(capture.sessions[1])}
        side = live_side_of(capture, segments, named, records)
        assert side.segments[0].venue_account_changes == [_change(moved)]
        assert derive_segments(capture)[0][0].venue_account_changes is None


class _Trades(AbstractPublicTradeReader):
    """A venue's trade list that gives up on one stretch."""

    def __init__(self, trades: List[VenueTrade], gives_up_at: Optional[datetime] = None):
        self._trades = trades
        self._gives_up_at = gives_up_at

    def read_trades(self, symbol, start, end):
        if start == self._gives_up_at:
            raise ConnectionGaveUpError('venue_public_trades gave up after 3 attempts')
        return [trade for trade in self._trades if start <= trade.executed_at < end]


class _Ledger(AbstractVenueLedgerReader):
    """An account's ledger that gives up."""

    def read_ledger(self, start, end):
        raise ConnectionGaveUpError("venue_ledger gave up: ['EAPI:Invalid key']")


class TestTheVenueSide:

    def test_each_run_is_answered_and_a_failed_one_is_named(self):
        trades = [_trade(101, 1), _trade(102, 2), _trade(201, 31)]
        collector = VenueSideCollector('kraken_spot', _Trades(trades, gives_up_at=_at(60)), None)
        late = DivergenceGap(source=GapSource.TRADE_ID_RUN, start=_at(60), end=_at(65),
                             confirmed=False, trade_id_before=300, trade_id_after=330)
        second = DivergenceGap(source=GapSource.TRADE_ID_RUN, start=_at(30), end=_at(35),
                               confirmed=False, trade_id_before=200, trade_id_after=210)
        answers = collector.answer_runs('ETHUSD', [_run(), second, late])
        assert answers.counts == {(100, 140): 2, (200, 210): 1}
        unreadable, = answers.unreadable
        assert 'trade ids 300–330' in unreadable and 'gave up' in unreadable

    def test_a_venue_without_a_trade_list_says_so_once(self):
        collector = VenueSideCollector('mt5', None, None)
        assert collector.answer_runs('EURUSD', [_run()]).counts == {}
        assert 'no trade list of the venue' in collector.bar_trades('EURUSD', []).unreadable[0]
        ledger = collector.read_venue_ledger(_at(0), _at(1))
        assert "the account's ledger has no reader" in ledger.unreadable

    def test_a_ledger_that_gave_up_is_never_an_empty_one(self):
        read = VenueSideCollector('kraken_spot', None, _Ledger()).read_venue_ledger(_at(0), _at(1))
        assert read.entries is None and 'EAPI:Invalid key' in read.unreadable

    def test_the_bars_trades(self):
        collector = VenueSideCollector('kraken_spot', _Trades([_trade(1, -30), _trade(2, 10)]),
                                       None)
        found = collector.bar_trades('ETHUSD', [(_at(-60), _at(0))])
        assert [trade.trade_id for trade in found.trades[_at(-60)]] == [1]
