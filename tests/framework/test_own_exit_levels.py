"""
Own exit levels — a decision logic's own stop and target per position (#625).

A venue holds a level for a position only where its API takes one; Kraken's takes none, and the
framework does not rebuild what a venue lacks. So a logic keeps its levels itself and closes
through its own close_position when the market reaches one. `OwnExitLevels` is that check,
written once. Its promise, pinned here:

    levels wait under the ENTRY ORDER's id, because that id becomes the position's
    a position without levels gets the ones its entry price and the seed distances make
    the stop only tightens
    the stop is checked before the target, both on the closing side of the book
    a position is closed once — never while its close is on its way
    the levels of a position that closed go with it
    a snapshot comes back only for the positions the framework restored

And `OwnExit`, the description of such a close, refuses a reason no level makes; a close sent
with one counts in `sl_tp_triggered` like a close the level check made — once.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional

import pytest

from python.framework.decision_logic.own_exit_levels import OwnExitLevels
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderResult,
    OrderStatus,
    RejectionReason,
)
from python.framework.types.trading_env_types.own_exit_types import OwnExit

_T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def _position(position_id: str = 'pos_1', direction: OrderDirection = OrderDirection.LONG,
              entry_price: float = 100.0) -> Position:
    """
    An open position.

    Args:
        position_id: Its id
        direction: LONG or SHORT
        entry_price: Its entry

    Returns:
        The position
    """
    return Position(position_id=position_id, symbol='BTCUSD', direction=direction, lots=0.1,
                    original_lots=0.1, entry_price=entry_price, entry_time=_T0)


def _tick(bid: float, ask: float) -> TickData:
    """
    A quote of the positions' symbol.

    Args:
        bid: Bid
        ask: Ask

    Returns:
        The tick
    """
    return TickData(timestamp=_T0, symbol='BTCUSD', bid=bid, ask=ask)


class _FakeTradingApi:
    """The four calls `enforce` makes, answered from a list of positions."""

    def __init__(self, positions: List[Position], refuse: bool = False):
        self.positions = positions
        self.pending: set = set()
        self.closes: List[tuple] = []
        self._refuse = refuse

    def get_open_positions(self, symbol: Optional[str] = None) -> List[Position]:
        return [p for p in self.positions if symbol is None or p.symbol == symbol]

    def is_pending_close(self, position_id: str) -> bool:
        return position_id in self.pending

    def close_position(self, position_id: str, lots=None, exit=None) -> OrderResult:
        self.closes.append((position_id, exit))
        if self._refuse:
            return OrderResult(order_id=position_id, status=OrderStatus.DENIED,
                               action=OrderAction.CLOSE,
                               rejection_reason=RejectionReason.POSITION_NOT_FOUND,
                               rejection_message='refused for the test')
        self.pending.add(position_id)
        return OrderResult(order_id=position_id, status=OrderStatus.PENDING,
                           action=OrderAction.CLOSE)


class TestTheLevelsWaitUnderTheEntryOrder:

    def test_levels_set_under_the_order_id_belong_to_its_position(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0, target=110.0)

        assert exits.due([_position('pos_1')], bid=94.9, ask=95.1) == [
            ('pos_1', OwnExit(CloseReason.SL_TRIGGERED, 95.0))]

    def test_without_levels_and_without_distances_nothing_is_checked(self):
        assert OwnExitLevels().due([_position()], bid=1.0, ask=1.1) == []


class TestAPositionWithoutLevelsIsSeeded:

    def test_a_long_gets_its_stop_below_and_its_target_above_the_entry(self):
        exits = OwnExitLevels(stop_distance=5.0, target_distance=10.0)
        exits.due([_position(entry_price=100.0)], bid=100.0, ask=100.1)

        levels = exits.get_levels('pos_1')
        assert (levels.stop, levels.target) == (95.0, 110.0)

    def test_a_short_the_mirror(self):
        exits = OwnExitLevels(stop_distance=5.0, target_distance=10.0)
        exits.due([_position(direction=OrderDirection.SHORT)], bid=100.0, ask=100.1)

        levels = exits.get_levels('pos_1')
        assert (levels.stop, levels.target) == (105.0, 90.0)

    @pytest.mark.parametrize('distance', [None, 0.0, -1.0])
    def test_a_distance_that_is_no_distance_gives_no_level(self, distance):
        """A band of zero width must not put the stop AT the entry, where it fires at once."""
        exits = OwnExitLevels(stop_distance=distance, target_distance=10.0)
        exits.due([_position()], bid=100.0, ask=100.1)

        assert exits.get_levels('pos_1').stop is None

    def test_set_levels_wins_over_the_seed(self):
        exits = OwnExitLevels(stop_distance=5.0)
        exits.set_levels('pos_1', stop=99.0)
        exits.due([_position()], bid=100.0, ask=100.1)

        assert exits.get_levels('pos_1').stop == 99.0


class TestTheStopOnlyTightens:

    @pytest.mark.parametrize('direction, tighter, looser', [
        (OrderDirection.LONG, 97.0, 94.0),
        (OrderDirection.SHORT, 103.0, 106.0),
    ])
    def test_toward_the_market_moves_away_from_it_does_not(self, direction, tighter, looser):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0 if direction == OrderDirection.LONG else 105.0)

        assert exits.move_stop('pos_1', direction, tighter) is True
        assert exits.move_stop('pos_1', direction, looser) is False
        assert exits.get_levels('pos_1').stop == tighter

    def test_a_position_without_a_stop_takes_the_first(self):
        exits = OwnExitLevels()

        assert exits.move_stop('pos_1', OrderDirection.LONG, 99.0) is True
        assert exits.get_levels('pos_1').stop == 99.0


class TestTheClosingSideOfTheBookDecides:
    """A long closes by selling into the bid, a short by buying the ask — the levels are the
    closing order's, as the simulation's own level check reads them."""

    def test_a_long_s_stop_reads_the_bid(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0)

        assert exits.due([_position()], bid=95.01, ask=94.0) == []
        assert exits.due([_position()], bid=95.0, ask=95.2) != []

    def test_a_short_s_stop_reads_the_ask(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=105.0)
        short = _position(direction=OrderDirection.SHORT)

        assert exits.due([short], bid=106.0, ask=104.99) == []
        assert exits.due([short], bid=104.8, ask=105.0) == [
            ('pos_1', OwnExit(CloseReason.SL_TRIGGERED, 105.0))]

    def test_a_long_s_target_reads_the_bid(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', target=110.0)

        assert exits.due([_position()], bid=110.0, ask=110.2) == [
            ('pos_1', OwnExit(CloseReason.TP_TRIGGERED, 110.0))]

    def test_the_stop_first_when_one_quote_reaches_both(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=100.0, target=100.0)

        assert exits.due([_position()], bid=100.0, ask=100.1)[0][1].reason == (
            CloseReason.SL_TRIGGERED)


class TestAPositionIsClosedOnce:

    def test_the_close_carries_the_exit(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0)
        api = _FakeTradingApi([_position()])

        exits.enforce(api, _tick(94.0, 94.1))

        assert api.closes == [('pos_1', OwnExit(CloseReason.SL_TRIGGERED, 95.0))]

    def test_never_while_its_close_is_on_its_way(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0)
        api = _FakeTradingApi([_position()])

        exits.enforce(api, _tick(94.0, 94.1))
        exits.enforce(api, _tick(93.0, 93.1))

        assert len(api.closes) == 1

    def test_a_refused_close_is_sent_again_on_the_next_tick(self):
        """The position is still open and still past its level — as the executor's own check
        behaved for a level the position held."""
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0)
        api = _FakeTradingApi([_position()], refuse=True)

        exits.enforce(api, _tick(94.0, 94.1))
        exits.enforce(api, _tick(94.0, 94.1))

        assert len(api.closes) == 2

    def test_only_the_tick_s_symbol(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0)
        api = _FakeTradingApi([_position()])

        exits.enforce(api, TickData(timestamp=_T0, symbol='ETHUSD', bid=1.0, ask=1.1))

        assert api.closes == []


class TestTheLevelsGoWithTheirPosition:

    def test_a_position_seen_and_gone_takes_its_levels(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=95.0)
        exits.due([_position()], bid=100.0, ask=100.1)

        exits.due([], bid=100.0, ask=100.1)

        assert exits.get_levels('pos_1') is None

    def test_an_entry_not_filled_yet_keeps_them(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_2', stop=95.0)

        exits.due([_position('pos_1')], bid=100.0, ask=100.1)

        assert exits.get_levels('pos_2').stop == 95.0

    def test_forget_drops_an_entry_that_will_not_fill(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_2', stop=95.0)

        exits.forget('pos_2')

        assert exits.get_levels('pos_2') is None


class TestASnapshotComesBackOnlyForRestoredPositions:

    def test_the_round_trip(self):
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=99.5, target=110.0)

        restored = OwnExitLevels()
        assert restored.restore(exits.snapshot(), {'pos_1'}) == 1
        levels = restored.get_levels('pos_1')
        assert (levels.stop, levels.target) == (99.5, 110.0)

    def test_a_position_that_did_not_come_back_is_dropped(self):
        """A logic never assumes a position from its own memory — the positions are the
        framework's."""
        exits = OwnExitLevels()
        exits.set_levels('pos_1', stop=99.5)
        exits.set_levels('pos_9', stop=50.0)

        restored = OwnExitLevels()
        assert restored.restore(exits.snapshot(), {'pos_1'}) == 1
        assert restored.get_levels('pos_9') is None

    def test_a_break_even_stop_survives_where_a_reseed_would_loosen_it(self):
        """The case the snapshot exists for: re-derived from the entry, the stop would be back
        at its initial distance."""
        exits = OwnExitLevels(stop_distance=5.0)
        exits.set_levels('pos_1', stop=95.0)
        exits.move_stop('pos_1', OrderDirection.LONG, 100.0)

        restored = OwnExitLevels(stop_distance=5.0)
        restored.restore(exits.snapshot(), {'pos_1'})
        restored.due([_position()], bid=101.0, ask=101.1)

        assert restored.get_levels('pos_1').stop == 100.0


class TestAnOwnExitNamesALevel:

    @pytest.mark.parametrize('reason', [CloseReason.MANUAL, CloseReason.SCENARIO_END])
    def test_a_reason_no_level_makes_is_refused(self, reason):
        with pytest.raises(ValueError, match='SL_TRIGGERED or TP_TRIGGERED'):
            OwnExit(reason, 95.0)

    @pytest.mark.parametrize('level', [0.0, -1.0])
    def test_a_level_that_is_no_price_is_refused(self, level):
        with pytest.raises(ValueError, match='positive price'):
            OwnExit(CloseReason.SL_TRIGGERED, level)


class _FakeExecutor:
    """What `DecisionTradingApi.close_position` asks of an executor, answering one result."""

    def __init__(self, result: OrderResult):
        self.broker = SimpleNamespace(get_order_capabilities=lambda: None)
        self.own_exits_counted = 0
        self.close_calls: List[dict] = []
        self._result = result

    def get_supported_order_types(self) -> frozenset:
        return frozenset()

    def add_order_outcome_listener(self, listener) -> None:
        pass

    def close_position(self, position_id: str, lots=None, **kwargs) -> OrderResult:
        self.close_calls.append(kwargs)
        return self._result

    def record_own_exit(self) -> None:
        self.own_exits_counted += 1


def _close_with_own_exit(result: OrderResult) -> _FakeExecutor:
    """
    Send one close at an own stop through the real trading API.

    Args:
        result: What the executor answers

    Returns:
        The executor, holding what it was asked and counted
    """
    executor = _FakeExecutor(result)
    api = DecisionTradingApi(executor, required_order_types=[])
    api.close_position('pos_1', exit=OwnExit(CloseReason.SL_TRIGGERED, 95.0))
    return executor


class TestAnOwnExitCountsAsATriggeredClose:

    def test_a_close_that_goes_out_counts_and_carries_its_level(self):
        executor = _close_with_own_exit(OrderResult(
            order_id='pos_1', status=OrderStatus.PENDING, action=OrderAction.CLOSE))

        assert executor.own_exits_counted == 1
        assert executor.close_calls == [
            {'close_reason': CloseReason.SL_TRIGGERED, 'exit_level': 95.0}]

    def test_a_refused_close_does_not(self):
        executor = _close_with_own_exit(OrderResult(
            order_id='pos_1', status=OrderStatus.DENIED, action=OrderAction.CLOSE,
            rejection_reason=RejectionReason.POSITION_NOT_FOUND, rejection_message='gone'))

        assert executor.own_exits_counted == 0

    def test_a_request_that_joins_a_close_on_its_way_does_not(self):
        """The close was counted when it went out; the second request sends nothing."""
        executor = _close_with_own_exit(OrderResult(
            order_id='pos_1', status=OrderStatus.PENDING, action=OrderAction.CLOSE,
            metadata={'joined_in_flight_close': True}))

        assert executor.own_exits_counted == 0

    def test_a_close_without_an_own_exit_is_not_counted_here(self):
        """A close the logic decided on any other ground is no stop and no target."""
        executor = _FakeExecutor(OrderResult(
            order_id='pos_1', status=OrderStatus.PENDING, action=OrderAction.CLOSE))
        DecisionTradingApi(executor, required_order_types=[]).close_position('pos_1')

        assert executor.own_exits_counted == 0
