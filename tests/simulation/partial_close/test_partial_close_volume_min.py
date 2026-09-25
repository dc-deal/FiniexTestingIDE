"""
A partial close is resolved BEFORE it is sent, never after it was filled (#507).

The defect this pins: a partial close whose remainder would fall below the symbol's `volume_min`
used to be converted into a FULL close at FILL time — one round trip after the venue had already
executed exactly the partial size it was asked for. The venue sold the part and kept the rest;
our books recorded the whole position as gone. Nothing re-syncs that.

The check itself was right — leaving an unsellable dust remainder helps nobody. Its PLACE was
wrong: made after the fill, it can no longer change what the venue was asked for, only what we
believe happened.

The arithmetic is sharper than it first looks. A valid partial needs BOTH `close_lots >=
volume_min` and `remaining >= volume_min`, so below `2 * volume_min` of position size **no valid
partial exists at all**. DOTUSD carries `volume_min` 3.9, which is why a perfectly ordinary
partial close lands there: 5.0 held, 2.0 requested, 1.1 left over.

Driven against the `TradeSimulator` over the real Kraken spot config — the venue whose crooked
minimums produce the case. MT5's round 0.01 barely can.
"""

from datetime import datetime, timedelta, timezone

import pytest

from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.simulation.trade_simulator import TradeSimulator
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import (
    OrderDirection,
    OrderStatus,
    RejectionReason,
)

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'

# DOTUSD: volume_min 3.9, contract_size 1.0, step 1e-08 — the shape the defect needs.
_SYMBOL = 'DOTUSD'
_QUOTE = 'USD'
_BASE = 'DOT'
_VOLUME_MIN = 3.9
_PRICE = 4.0
_HELD = 5.0
_REQUESTED = 2.0                 # leaves 3.0, i.e. under the minimum
_START_QUOTE = 1_000.0
_CLOCK = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class _NullLogger:
    """Minimal duck-typed logger — the arithmetic under test does not log."""

    def verbose(self, *args, **kwargs): pass
    def debug(self, *args, **kwargs): pass
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass


def _simulator(held: float = _HELD) -> TradeSimulator:
    """
    A Kraken spot simulator already holding one position.

    Args:
        held: Position size in the base asset

    Returns:
        A TradeSimulator with one open LONG, marked at the working price
    """
    simulator = TradeSimulator(
        broker_config=BrokerConfigFactory.build_broker_config(_KRAKEN_CONFIG),
        initial_balance=_START_QUOTE, account_currency=_QUOTE, logger=_NullLogger(),
        spot_mode=True, initial_balances={_QUOTE: _START_QUOTE, _BASE: held})
    simulator.on_tick(_tick(0))
    simulator.portfolio.open_positions['p1'] = _position(held)
    simulator.portfolio.mark_dirty(_tick(0))
    return simulator


def _position(lots: float):
    """A plain LONG of `lots` at the working price. Returns: the Position."""
    from python.framework.types.portfolio_types.portfolio_types import Position
    return Position(
        position_id='p1', symbol=_SYMBOL, direction=OrderDirection.LONG, lots=lots,
        original_lots=lots, entry_price=_PRICE, entry_time=_CLOCK,
        entry_tick_value=1.0, digits=4, contract_size=1)


def _tick(index: int) -> TickData:
    """
    A tick `index` seconds after the fixed clock.

    `collected_msc` is what the latency simulator reads, not `timestamp` — without it every
    submitted order stays due forever and the harness silently tests nothing.

    Returns:
        The TickData
    """
    stamp = _CLOCK + timedelta(seconds=index)
    return TickData(
        timestamp=stamp, symbol=_SYMBOL, bid=_PRICE, ask=_PRICE,
        collected_msc=int(stamp.timestamp() * 1000))


def _pump(simulator: TradeSimulator, ticks: int = 60) -> None:
    """Advance the simulator far enough for a submitted order to resolve."""
    for index in range(1, ticks + 1):
        simulator.on_tick(_tick(index))


class TestTheRequestIsRefusedBeforeItReachesTheVenue:
    """
    The resolution moved to submission, so what is asked for and what is booked agree.

    Operator decision 2026-09-23: REFUSE rather than round up to a full close or clamp the
    size. Rounding up would let the framework turn a partial exit into the largest possible
    position change; clamping is arithmetically impossible below `2 * volume_min` and would
    need the refusal as a fallback anyway — two rules where one will do.
    """

    def test_the_partial_is_rejected_and_names_why(self):
        simulator = _simulator()

        result = simulator.close_position('p1', lots=_REQUESTED)

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.REMAINDER_BELOW_MINIMUM

    def test_the_message_carries_the_arithmetic_a_decision_needs(self):
        """
        Both bounds are stated even though they contradict each other here — that IS the
        answer. Without them the refusal reads as a rounding problem rather than as 'no valid
        partial exists at this position size'.
        """
        simulator = _simulator()

        message = simulator.close_position('p1', lots=_REQUESTED).rejection_message or ''

        for number in (f'{_REQUESTED:.8f}', f'{_HELD:.8f}', f'{_VOLUME_MIN:.8f}'):
            assert number in message, f'{number} missing from: {message}'
        # In LOTS, never in the base asset. The two are equal only where contract_size is
        # 1.0 — true here on Kraken spot, false on MT5, and this message is written in the
        # shared executor that both account models traverse (§31b).
        assert 'lots' in message
        assert _BASE not in message, (
            f'a lot figure labelled as the base asset understates an MT5 size by its '
            f'contract size: {message}')

    def test_the_position_is_untouched(self):
        """A refusal that still moved the books would be the defect with extra steps."""
        simulator = _simulator()

        simulator.close_position('p1', lots=_REQUESTED)
        _pump(simulator)

        position = simulator.portfolio.get_position('p1')
        assert position is not None, 'the position was closed by a refused request'
        assert position.lots == pytest.approx(_HELD)
        assert simulator.portfolio.get_asset_balance(_BASE) == pytest.approx(_HELD)

    def test_nothing_was_sent(self):
        """The whole point of moving the check: the venue is never asked."""
        simulator = _simulator()

        simulator.close_position('p1', lots=_REQUESTED)

        assert not simulator.has_pending_orders()

    def test_a_position_big_enough_for_partials_still_refuses_a_bad_split(self):
        """
        The ordinary case, distinct from the structural one above.

        10.0 held clears twice the minimum, so partials ARE possible here — 3.9 to 6.1. A
        request for 7.0 is not one of them, and the refusal names the window rather than
        declaring partials impossible.
        """
        simulator = _simulator(held=10.0)

        result = simulator.close_position('p1', lots=7.0)

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.REMAINDER_BELOW_MINIMUM
        message = result.rejection_message
        assert '3.00000000' in message, f'the stranded remainder is missing: {message}'
        assert '6.10000000' in message, f'the upper bound is missing: {message}'
        assert 'no partial is possible' not in message, (
            'this position CAN be closed partially — saying otherwise sends the caller away '
            f'from a size that exists: {message}')

    def test_a_close_below_the_minimum_is_refused_as_an_invalid_size(self):
        """
        The second gap, which the close path did not check at all.

        A 1.0 DOT close is below `volume_min` on its own; the venue would refuse it. Naming
        it here, locally, costs a round trip less and says which rule was broken.
        """
        simulator = _simulator(held=20.0)

        result = simulator.close_position('p1', lots=1.0)

        assert result.status == OrderStatus.REJECTED
        assert result.rejection_reason == RejectionReason.INVALID_LOT_SIZE


class TestAValidPartialStillGoesThrough:
    """
    The guard against overcorrecting — a refusal that refused everything would pass the cases
    above and break the feature.
    """

    def test_a_partial_leaving_at_least_the_minimum_is_booked_as_requested(self):
        simulator = _simulator(held=10.0)

        result = simulator.close_position('p1', lots=4.0)
        _pump(simulator)

        assert result.status == OrderStatus.PENDING
        position = simulator.portfolio.get_position('p1')
        assert position is not None, 'a valid partial closed the whole position'
        assert position.lots == pytest.approx(6.0), (
            'the booked size is not the requested size — that is the invariant this issue '
            'exists to restore')

    def test_a_full_close_is_never_affected(self):
        """`lots=None` asks for everything and has no remainder to judge."""
        simulator = _simulator()

        result = simulator.close_position('p1')
        _pump(simulator)

        assert result.status == OrderStatus.PENDING
        assert simulator.portfolio.get_position('p1') is None

    def test_closing_exactly_the_whole_position_by_size_is_a_full_close(self):
        """The boundary: `lots == position.lots` leaves nothing, so nothing is unsellable."""
        simulator = _simulator()

        result = simulator.close_position('p1', lots=_HELD)
        _pump(simulator)

        assert result.status == OrderStatus.PENDING
        assert simulator.portfolio.get_position('p1') is None
