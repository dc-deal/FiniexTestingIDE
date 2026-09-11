"""
Kraken Adapter — Dry-Run Fill Rules (#505 · 4)

`dry_run: true` is the shipped default for `kraken_spot`, and in that mode nothing at the venue
holds the order — `DryRunOrderSimulator` plays the venue. It used to play it blind: every order
flipped to FILLED after two polls, and a MARKET order filled at `0.0`. With `poll_interval_ms
= 5000` that meant every resting order "filled" about ten seconds after placement at a price
nobody chose, and the first rehearsal of any resting-order feature reported a stop that fired
at zero.

What these tests pin is the venue's actual behaviour: an order fills when the MARKET reaches its
price, and not before. Where the simulator cannot tell — no price to compare against — it must
refuse rather than invent, because a refusal in a rehearsal is information and a fabricated fill
is worse than no rehearsal.

The trigger comparison itself is not duplicated here: it is the same predicate the backtest uses
(`utils/trading_math/price_trigger.py`), so a dry-run rehearsal and a backtest agree by
construction.

Pure offline — the simulator is constructed directly. No credentials, no HTTP, no adapter.
"""

from datetime import datetime, timezone

import pytest

from python.framework.trading_env.adapters.dry_run_simulator import DryRunOrderSimulator
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType

_TS = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
_SYMBOL = 'ETHUSD'


def _market(bid: float, ask: float) -> TickData:
    """
    One market observation.

    Args:
        bid: Bid price
        ask: Ask price

    Returns:
        A TickData carrying that quote
    """
    return TickData(timestamp=_TS, symbol=_SYMBOL, bid=bid, ask=ask)


def _submit(sim, order_type, direction=OrderDirection.LONG, *,
            limit_price=None, trigger_price=None):
    """
    Register one order of a given type.

    Args:
        sim: The simulator under test
        order_type: Which fill rule to exercise
        direction: LONG or SHORT
        limit_price: The limit of a LIMIT, or a STOP_LIMIT's second leg
        trigger_price: The trigger of a STOP or STOP_LIMIT

    Returns:
        The submit BrokerResponse
    """
    return sim.submit(
        lots=1.0, timestamp=_TS, direction=direction, order_type=order_type,
        limit_price=limit_price, trigger_price=trigger_price)


def _poll(sim: DryRunOrderSimulator, ref: str, market, times: int = 1):
    """
    Poll an order `times` times and return the last response.

    Args:
        sim: The simulator under test
        ref: Synthetic broker reference
        market: TickData for every poll, or None
        times: How many polls to perform

    Returns:
        The final BrokerResponse
    """
    response = None
    for _ in range(times):
        response = sim.query(ref, _TS, market=market)
        # Stop at the first terminal answer, the way the executor does: it drops the order
        # on FILLED, so polling past a fill is something no caller ever does — and it would
        # hand the test the retired-reference answer instead of the fill.
        if response.status != BrokerOrderStatus.PENDING:
            return response
    return response


class TestAMarketOrderFillsAtARealPrice:
    """The case that produced `0.0` — a fill price nobody chose."""

    def test_a_buy_fills_at_the_ask(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.MARKET)

        response = _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=2)

        assert response.status == BrokerOrderStatus.FILLED
        assert response.fill_price == pytest.approx(4001.0), 'a buy pays the ask'

    def test_a_sell_fills_at_the_bid(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.MARKET, OrderDirection.SHORT)

        response = _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=2)

        assert response.fill_price == pytest.approx(3999.0), 'a sell receives the bid'

    def test_it_fills_at_the_price_when_it_ARRIVES_not_when_it_was_sent(self):
        """
        The one the first attempt got backwards.

        A market order is priced on arrival. The simulation stores `entry_price = 0` for a
        market order and fills from the tick current AFTER the latency
        (`order_latency_simulator.py` / `trade_simulator.py:326`), and live Kraken reports the
        execution price. Pricing at submit made the rehearsal show zero round-trip slippage by
        construction — with `poll_interval_ms = 5000` there are seconds between the two, which
        on crypto is a real distance.
        """
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.MARKET)
        _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=1)

        response = _poll(sim, submit.broker_ref, _market(bid=4500.0, ask=4502.0), times=1)

        assert response.fill_price == pytest.approx(4502.0), 'the ask at the FILL'


class TestARestingOrderWaitsForItsPrice:
    """
    The defect in one sentence: a resting order used to fill because time passed, not because
    the market arrived.
    """

    def test_a_buy_limit_the_market_never_reaches_never_fills(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.LIMIT, limit_price=3900.0)

        response = _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=20)

        assert response.status == BrokerOrderStatus.PENDING, (
            'twenty polls is ~100 s of tick loop — a limit 100 below the ask must still wait')

    def test_a_buy_limit_fills_once_the_ask_comes_down_to_it(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.LIMIT, limit_price=3900.0)
        _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=5)

        response = _poll(sim, submit.broker_ref, _market(bid=3898.0, ask=3900.0), times=1)

        assert response.status == BrokerOrderStatus.FILLED
        assert response.fill_price == pytest.approx(3900.0), 'a limit fills at its own price'

    def test_a_sell_stop_whose_trigger_is_never_crossed_never_fills(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.STOP, OrderDirection.SHORT, trigger_price=3500.0)

        response = _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=20)

        assert response.status == BrokerOrderStatus.PENDING, (
            'this is the protective stop of #503 — a rehearsal must not report it as fired')

    def test_a_sell_stop_fills_at_the_MARKET_not_at_its_trigger(self):
        """
        The other one the first attempt got backwards.

        A stop is an instruction to trade once a level is passed, and by the time it is
        passed the price is already beyond it. The simulation says so in its own comment,
        `# STOP triggered → fill at current market price`
        (`trade_simulator.py:228-238`). Filling at the trigger flattered every stop by exactly
        the distance the market had moved through it — on a gap, by the whole gap.
        """
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.STOP, OrderDirection.SHORT, trigger_price=3500.0)
        _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=5)

        # The bid gaps 500 THROUGH the trigger, which is what a stop is for.
        response = _poll(sim, submit.broker_ref, _market(bid=3000.0, ask=3002.0), times=1)

        assert response.status == BrokerOrderStatus.FILLED
        assert response.fill_price == pytest.approx(3000.0), (
            'the bid it can actually sell at, not the 3500 it wished for')

    def test_a_stop_limit_that_the_market_gapped_PAST_never_fills(self):
        """
        The only stateful branch, and the real stop-limit trap: the trigger fires, and the
        price is already below the limit, so the order can no longer be filled at all. A
        protective stop-limit in a crash does exactly this — which is precisely what a
        rehearsal has to be able to show, instead of a comfortable fill.

        A SELL limit fills when the bid is at or ABOVE it (selling higher than your limit is
        welcome), so the window that waits is a bid BELOW the limit.
        """
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.STOP_LIMIT, OrderDirection.SHORT,
                         trigger_price=3500.0, limit_price=3450.0)
        _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=3)

        # The bid gaps from 3999 straight to 3400: through the trigger AND past the limit.
        gapped = _poll(sim, submit.broker_ref, _market(bid=3400.0, ask=3402.0), times=5)

        assert gapped.status == BrokerOrderStatus.PENDING, (
            'triggered but unfillable — the position stays open, which is the whole risk of '
            'a stop-limit and the thing a rehearsal must not hide')

    def test_a_stop_limit_fills_at_its_limit_once_triggered(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.STOP_LIMIT, OrderDirection.SHORT,
                         trigger_price=3500.0, limit_price=3450.0)
        _poll(sim, submit.broker_ref, _market(bid=3999.0, ask=4001.0), times=3)

        filled = _poll(sim, submit.broker_ref, _market(bid=3480.0, ask=3482.0))

        assert filled.status == BrokerOrderStatus.FILLED
        assert filled.fill_price == pytest.approx(3450.0), 'the limit, not the trigger'


class TestWhatItCannotDecideItRefuses:
    """
    A fabricated fill at 0.0 is worse than no rehearsal at all — so where the simulator has no
    price to compare against, it stays PENDING and SAYS so. The adapter has no logger by design,
    so the reason travels on the response and the executor is what makes it visible (§35).
    """

    def test_no_market_price_means_no_fill(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.MARKET)

        response = _poll(sim, submit.broker_ref, None, times=10)

        assert response.status == BrokerOrderStatus.PENDING
        assert response.fill_price is None, 'never invent a price'

    def test_the_refusal_carries_its_reason(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.MARKET)

        response = _poll(sim, submit.broker_ref, None, times=2)

        assert response.undecided_reason, 'a silent PENDING is indistinguishable from waiting'

    def test_a_trailing_stop_is_refused_rather_than_guessed(self):
        """
        Nothing in this project models a trailing stop's venue-side behaviour. Inventing one
        here would be a second, unvalidated answer.
        """
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.TRAILING_STOP, OrderDirection.SHORT,
                         trigger_price=3500.0)

        response = _poll(sim, submit.broker_ref, _market(bid=3000.0, ask=3002.0), times=10)

        assert response.status == BrokerOrderStatus.PENDING
        assert response.undecided_reason

    def test_a_reference_it_never_issued_is_refused_not_reported_filled(self):
        """
        The last fabricated fill in the module, and it had a real path to it: a restart plus
        boot adoption (#355) puts a DRYRUN-* ref from a previous session into the shadow book,
        and the old code answered any unknown ref FILLED — with no price, and it got booked.
        """
        sim = DryRunOrderSimulator()

        response = sim.query('DRYRUN-424242', _TS, market=_market(3999.0, 4001.0))

        assert response.status == BrokerOrderStatus.PENDING
        assert 'never issued' in response.undecided_reason


class TestTheLifecycleShapeIsUnchanged:
    """What already worked and must keep working — the reason this class exists at all."""

    def test_a_submit_is_pending_with_a_synthetic_ref(self):
        sim = DryRunOrderSimulator()

        response = _submit(sim, OrderType.MARKET)

        assert response.status == BrokerOrderStatus.PENDING
        assert response.broker_ref.startswith('DRYRUN-')

    def test_refs_do_not_collide(self):
        sim = DryRunOrderSimulator()
        refs = {_submit(sim, OrderType.MARKET).broker_ref for _ in range(5)}

        assert len(refs) == 5

    def test_cancel_is_idempotent(self):
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.LIMIT, limit_price=3900.0)

        first = sim.cancel(submit.broker_ref, _TS)
        second = sim.cancel(submit.broker_ref, _TS)

        assert first.status == BrokerOrderStatus.CANCELLED
        assert second.status == BrokerOrderStatus.CANCELLED

    def test_an_amend_keeps_the_same_ref_and_moves_the_limit(self):
        """Kraken AmendOrder is in-place — the ref survives, which the executor relies on."""
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.LIMIT, limit_price=3900.0)

        amended = sim.modify(submit.broker_ref, _TS, new_limit_price=4001.0)
        filled = _poll(sim, submit.broker_ref, _market(3999.0, 4001.0), times=2)

        assert amended.broker_ref == submit.broker_ref
        assert filled.status == BrokerOrderStatus.FILLED, 'the amended limit is now reachable'
        assert filled.fill_price == pytest.approx(4001.0)

    def test_amending_a_stop_moves_the_TRIGGER_and_not_the_limit(self):
        """
        The two legs must not cross. One shared `price` field meant a limit-amend overwrote
        the trigger, and an amended trigger never arrived at all — so a rehearsal kept
        watching the old level while our books showed the new one.
        """
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.STOP_LIMIT, OrderDirection.SHORT,
                         trigger_price=3500.0, limit_price=3450.0)

        sim.modify(submit.broker_ref, _TS, new_trigger_price=3900.0)

        # A bid of 3890 crosses the NEW trigger (3900) and would not have crossed the old
        # one (3500) — so a fill here proves the trigger moved. And it fills at 3450, which
        # proves the amend did not overwrite the limit leg with 3900.
        filled = _poll(sim, submit.broker_ref, _market(bid=3890.0, ask=3892.0), times=3)

        assert filled.status == BrokerOrderStatus.FILLED, 'the amended trigger was crossed'
        assert filled.fill_price == pytest.approx(3450.0), 'the limit leg survived the amend'

    def test_a_ref_this_simulator_retired_still_reads_as_filled(self):
        """
        A re-query after the fill removed the order must not read as a false PENDING — the
        legacy behaviour callers depend on. It applies to a ref we ISSUED and filled, never
        to one we have never seen.
        """
        sim = DryRunOrderSimulator()
        submit = _submit(sim, OrderType.MARKET)
        _poll(sim, submit.broker_ref, _market(3999.0, 4001.0), times=2)

        again = sim.query(submit.broker_ref, _TS, market=_market(3999.0, 4001.0))

        assert again.status == BrokerOrderStatus.FILLED
        assert again.undecided_reason is None
