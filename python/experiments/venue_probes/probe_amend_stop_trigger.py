"""
Venue probe — which AmendOrder field moves a resting STOP's trigger on Kraken? (#503 / #500)

WHY THIS EXISTS
The release-gate test `test_stop_order_lifecycle` places a real standalone stop, amends its
trigger and reads it back. On 2026-09-10 the amend was ACCEPTED and the trigger did not move:
we sent `trigger_price=8500` against a resting `stop-loss` at 8000, Kraken answered without a
rejection, and the order still read back `descr.price = '8000.00'` with `price2 = '0'`.

Kraken's AddOrder and AmendOrder do not use the same vocabulary, and that is the suspicion this
probe measures rather than argues:

    AddOrder,  ordertype=stop-loss        → `price`  IS the trigger        (measured, #500)
    AddOrder,  ordertype=stop-loss-limit  → `price`  trigger, `price2` limit
    AmendOrder                            → `trigger_price` and `limit_price`

If AmendOrder's `limit_price` maps to the order's `price` field, then for a plain `stop-loss`
— where `price` IS the trigger — `limit_price` is the field that moves the trigger, and
`trigger_price` applies only to the `*-limit` variants. #500 moved every triggered type onto
`trigger_price`, which would be right for `stop-loss-limit` and wrong for `stop-loss`.

WHAT IT COSTS
Nothing in fees. The stop is placed far ABOVE the market so it cannot trigger, and it is
cancelled in a `finally` block whatever happens. Only the order's value (lots x trigger) is
briefly reserved and released on cancel.

Reaching `_fetch_private` directly is deliberate (§32): what is measured is the raw wire answer,
and the parse layer is what flattens exactly that. The public read is called beside it so the
probe also shows what production makes of the same answer.

    python python/experiments/venue_probes/probe_amend_stop_trigger.py

MEASUREMENTS
  (append each run here, dated, newest last — a repeated measurement is a series, not a surprise)

  2026-09-10 12:00 UTC — the suspicion above is REFUTED, and the real finding is worse to catch.
    AmendOrder(trigger_price=8500) on a resting `stop-loss` at 8000  → descr.price '8500.00'.
    The trigger MOVES. `trigger_price` is the correct field and #500's mapping is right.
    AmendOrder(limit_price=9000) on the same order  → `EOrder:Invalid limit price`, refused,
    which is correct: a plain stop-loss has no limit to amend.
    ETHUSD printed 2460.88, so the 8000 trigger sat 3.25x above the market and could not fire.
    Cost: nothing. No fill, cancelled in the finally block.

    SO WHY DID THE RELEASE GATE FAIL? `test_stop_order_lifecycle` read the trigger back as
    8000 immediately after an ACCEPTED amend, and PASSED on a re-run minutes later with the
    same code. **Kraken's OpenOrders can lag an accepted AmendOrder.** Measured once as a lag,
    once (this probe) as immediate — so it is intermittent, not a constant.
    Two consequences: the release-gate test is FLAKY, and a gate that fails intermittently is
    a gate that gets ignored. And #503 stage D amends a live protective order's trigger — a
    poll that reads back the pre-amend level would make the reconciler report a divergence
    that does not exist, or make us believe a stop sits where it does not.
"""

import json
from typing import Any, Dict, Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter

_BROKER_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'
_PAIR = 'ETHUSD'
_LOTS = '0.001'
# Far above any plausible ETH print, so the buy stop can never trigger inside the probe.
_TRIGGER_INITIAL = 8000.0
_TRIGGER_VIA_TRIGGER_PRICE = 8500.0
_TRIGGER_VIA_LIMIT_PRICE = 9000.0


def _build_adapter() -> KrakenAdapter:
    """
    A live-enabled Kraken adapter reading the same settings a live session reads.

    Returns:
        The adapter, dry_run forced OFF
    """
    with open(_BROKER_CONFIG, encoding='utf-8') as handle:
        broker_config = json.load(handle)
    entry = MarketConfigManager().get_broker_entry('kraken_spot')
    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=entry.credentials_file,
        dry_run=False,
        transport=entry.broker_transport.model_copy(
            update={'rate_limit_interval_s': 0.5}),
    )
    return adapter


def _resting(adapter: KrakenAdapter, txid: str) -> Optional[Dict[str, Any]]:
    """
    The venue's raw record for one resting order.

    Args:
        adapter: Live adapter
        txid: The order's reference

    Returns:
        Kraken's own dict for that order, or None when it is not resting
    """
    raw = adapter._fetch_private('/0/private/OpenOrders', {})
    return (raw.get('open') or {}).get(txid)


def _report(label: str, info: Optional[Dict[str, Any]]) -> None:
    """
    Print the two price fields the question is about.

    Args:
        label: What was just attempted
        info: Kraken's raw record, or None
    """
    if info is None:
        print(f'  {label}: NOT RESTING')
        return
    descr = info.get('descr', {})
    print(f"  {label}: price={descr.get('price')!r}  price2={descr.get('price2')!r}  "
          f"| {descr.get('order')}")


def main() -> None:
    """Place a stop, amend its trigger two ways, report what moved, then cancel."""
    adapter = _build_adapter()
    txid = None
    try:
        placed = adapter._fetch_private('/0/private/AddOrder', {
            'pair': _PAIR,
            'type': 'buy',
            'ordertype': 'stop-loss',
            'price': str(_TRIGGER_INITIAL),
            'volume': _LOTS,
        })
        txid = (placed.get('txid') or [None])[0]
        print(f'placed stop {txid} at trigger {_TRIGGER_INITIAL}')
        _report('as placed', _resting(adapter, txid))

        answer = adapter._fetch_private('/0/private/AmendOrder', {
            'txid': txid,
            'trigger_price': str(_TRIGGER_VIA_TRIGGER_PRICE),
        })
        print(f'  AmendOrder(trigger_price={_TRIGGER_VIA_TRIGGER_PRICE}) → {answer}')
        _report(f'after trigger_price={_TRIGGER_VIA_TRIGGER_PRICE}', _resting(adapter, txid))

        answer = adapter._fetch_private('/0/private/AmendOrder', {
            'txid': txid,
            'limit_price': str(_TRIGGER_VIA_LIMIT_PRICE),
        })
        print(f'  AmendOrder(limit_price={_TRIGGER_VIA_LIMIT_PRICE}) → {answer}')
        _report(f'after limit_price={_TRIGGER_VIA_LIMIT_PRICE}', _resting(adapter, txid))

        # What PRODUCTION makes of the same order, through the parse layer.
        parsed = [o for o in adapter.get_broker_orders() if o.broker_ref == txid]
        if parsed:
            order = parsed[0]
            print(f'  parsed by our adapter: order_type={order.order_type.value} '
                  f'stop_price={order.stop_price} price={order.price}')
    finally:
        if txid:
            adapter._fetch_private('/0/private/CancelOrder', {'txid': txid})
            print(f'cancelled {txid}')


if __name__ == '__main__':
    main()
