"""
Venue probe — is a filled order's FEE already in the status answer that first says FILLED? (#508)

WHY THIS EXISTS
#508 books a live fill from the venue's own fee instead of our estimate, and takes that fee from
the same QueryOrders answer that reports the order closed — no second request, no waiting. The
documentation describes the order's `fee` only as "Total fee (quote currency)" and says nothing
about WHEN it is populated. Read afterwards it is right: measured 2026-10-11 over the 13 orders of
the field study of 2026-10-07, every order's fee equalled the sum of its trades' fees, and the
ledger debited it in ZUSD. What no read afterwards can show is the moment the booking depends on:
the FIRST poll that says closed.

The same round trip answers three more questions, so the probe records them beside it:
  - which asset the account was debited the fee in, per side — the documentation names the base
    currency as the default when selling (`fcib`), while the field study's ledger shows ZUSD on
    every sell and every order reads back `oflags='fciq'` although we send no flags
  - the venue's maker flag, which exists per execution only, never on the order
  - the ledger's own figure, which rounds to the asset's precision

WHAT IT COSTS
A market buy of 0.002 ETHUSD and the market sell that undoes it: two taker fees, about 0.08 USD
at 2,600 USD per ETH, plus the spread crossed twice. The buy's reference is kept the moment the
venue names it, and the sell runs in a `finally` block for whatever a fresh read says the buy
executed — so a read that fails on the way leaves no holding behind. One case it cannot cover: an
AddOrder whose answer is lost names no reference to ask about. The probe then stops and says so,
and the account is checked by hand.

Reaching `_fetch_private` directly is deliberate: what is measured is the raw wire answer, which
the parse layer flattens. Each closed answer is also handed to the adapter's own
`parse_query_response`, so the probe shows what production makes of the same answer.

    python python/experiments/venue_probes/probe_kraken_fee_at_fill.py

MEASUREMENTS
  (append each run here, dated, newest last — a repeated measurement is a series, not a surprise)

  2026-10-11 — the fee IS in the first answer that says closed, on both sides.
    BUY  ORVS4M-TG7MT-BELUXN: 0.38 s no entry yet (the venue did not name the txid — our parser
         reads that as UNKNOWN, not as a state); 0.64 s closed, vol_exec 0.002, fee 0.03998,
         one trade id; the same fee on every later read.
    SELL OM72BX-3J4EO-ZYDHVG: 0.49 s closed, fee 0.03997, one trade id; unchanged after.
    Executions: each trade's fee equals its order's fee; maker=False on both (market orders).
    Ledger: the fee was debited in ZUSD on BOTH sides (XETH fee 0), and both orders read back
    `oflags='fciq'` although none was sent — the documented sell-side default `fcib` is not
    what this account does. The ledger rounds to ZUSD's four decimals: fee 0.03998 and 0.03997
    were each debited as 0.0400, cost 4.99726 as 4.9973 — under 0.0001 USD per fill.
    Cost of the run: 0.08 USD in fees, 0.00002 USD of spread (2498.63 bought, 2498.62 sold).
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter

_ROOT = Path(__file__).resolve().parents[3]
_BROKER_CONFIG = _ROOT / 'configs/brokers/kraken/kraken_spot_broker_config.json'
_PAIR = 'ETHUSD'
_LOTS = '0.002'
# Fast enough to catch the transition from open to closed, few enough to stay inside the venue's
# private-call allowance: twelve reads in three seconds, then stop once the answer has settled.
_POLL_INTERVAL_S = 0.25
_MAX_READS = 12
_CLOSED_READS_TO_SETTLE = 3


def _build_adapter() -> KrakenAdapter:
    """
    A live-enabled Kraken adapter reading the same settings a live session reads.

    Returns:
        The adapter, dry_run forced off, with a short spacing between private calls
    """
    broker_config = json.loads(_BROKER_CONFIG.read_text(encoding='utf-8'))
    entry = MarketConfigManager().get_broker_entry('kraken_spot')
    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=entry.credentials_file,
        dry_run=False,
        transport=entry.broker_transport.model_copy(
            update={'rate_limit_interval_s': _POLL_INTERVAL_S}),
    )
    return adapter


def _poll_until_settled(adapter: KrakenAdapter, txid: str, sent_at: float) -> List[Dict[str, Any]]:
    """
    Read one order's status answer repeatedly from the moment it was sent.

    Args:
        adapter: Live adapter
        txid: The order's reference
        sent_at: Monotonic time the order was sent

    Returns:
        One row per read: elapsed seconds, status, executed volume, cost, fee, trades
    """
    reads: List[Dict[str, Any]] = []
    closed_reads = 0
    while len(reads) < _MAX_READS and closed_reads < _CLOSED_READS_TO_SETTLE:
        try:
            raw = adapter._fetch_private('/0/private/QueryOrders',
                                         {'txid': txid, 'trades': 'true'})
        except Exception as error:
            # A failed read is a row of its own, never the end of the probe: the order is out
            # there, and the sell that undoes it must still run
            reads.append({'elapsed_s': time.monotonic() - sent_at, 'status': f'error: {error}',
                          'vol_exec': None, 'cost': None, 'fee': None, 'oflags': None,
                          'trades': [], 'raw': {}})
            continue
        info = raw.get(txid) or {}
        status = info.get('status')
        reads.append({
            'elapsed_s': time.monotonic() - sent_at,
            'status': status,
            'vol_exec': info.get('vol_exec'),
            'cost': info.get('cost'),
            'fee': info.get('fee'),
            'oflags': info.get('oflags'),
            'trades': info.get('trades') or [],
            'raw': raw,
        })
        if status == 'closed':
            closed_reads += 1
    return reads


def _send(adapter: KrakenAdapter, side: str, volume: str) -> Optional[str]:
    """
    Send one market order.

    Args:
        adapter: Live adapter
        side: 'buy' or 'sell'
        volume: The volume, as the venue takes it

    Returns:
        The reference the venue named, or None where it named none
    """
    placed = adapter._fetch_private('/0/private/AddOrder', {
        'pair': _PAIR, 'type': side, 'ordertype': 'market', 'volume': volume})
    return (placed.get('txid') or [None])[0]


def _executed_volume(adapter: KrakenAdapter, txid: str) -> float:
    """
    What an order executed, read fresh — the sell undoes exactly that.

    Args:
        adapter: Live adapter
        txid: The order's reference

    Returns:
        The executed volume; the full probe size where no read answers, because selling what
        might be held is the cheaper mistake than leaving it
    """
    for _ in range(3):
        try:
            info = adapter._fetch_private('/0/private/QueryOrders', {'txid': txid}).get(txid) or {}
            return float(info.get('vol_exec') or 0.0)
        except Exception as error:
            print(f'read of {txid} failed ({error}) — asking again')
            time.sleep(1.0)
    return float(_LOTS)


def _leg(adapter: KrakenAdapter, side: str, txid: str, sent_at: float) -> Dict[str, Any]:
    """
    Read one sent order until its answer has settled.

    Args:
        adapter: Live adapter
        side: 'buy' or 'sell'
        txid: The reference the venue named
        sent_at: Monotonic time the order was sent

    Returns:
        The order's reference, its reads, and what our parser makes of its first closed answer
    """
    reads = _poll_until_settled(adapter, txid, sent_at)
    first_closed = next((read for read in reads if read['status'] == 'closed'), None)
    parsed = (adapter.parse_query_response(first_closed['raw'], txid, datetime.now(timezone.utc))
              if first_closed else None)
    return {'side': side, 'txid': txid, 'reads': reads, 'parsed': parsed}


def _executed(leg: Optional[Dict[str, Any]]) -> bool:
    """
    Whether a leg executed any volume.

    Args:
        leg: The leg, or None when it was never sent

    Returns:
        True when any read reports executed volume
    """
    if leg is None:
        return False
    return any(float(read.get('vol_exec') or 0.0) > 0.0 for read in leg['reads'])


def _report_leg(leg: Dict[str, Any]) -> None:
    """
    Print one leg's reads and the verdict on its first closed answer.

    Args:
        leg: The leg
    """
    print(f"\n{leg['side'].upper()} {leg['txid']}")
    print(f"  {'after':>7}  {'status':8} {'vol_exec':>11} {'cost':>9} {'fee':>9}  oflags  trades")
    for read in leg['reads']:
        print(f"  {read['elapsed_s']:6.2f}s  {str(read['status']):8} {str(read['vol_exec']):>11} "
              f"{str(read['cost']):>9} {str(read['fee']):>9}  {read['oflags']!s:6}  "
              f"{len(read['trades'])}")
    closed = [read for read in leg['reads'] if read['status'] == 'closed']
    if not closed:
        print('  VERDICT: never read as closed inside the window')
        return
    fees = {read['fee'] for read in closed}
    first = closed[0]
    print(f"  VERDICT: first closed read at {first['elapsed_s']:.2f}s carries fee={first['fee']} "
          f"and {len(first['trades'])} trade id(s); fee across the closed reads: {sorted(fees)}")
    if leg['parsed'] is not None:
        parsed = leg['parsed']
        print(f"  our parser on that answer: status={parsed.status.value} "
              f"fill_price={parsed.fill_price} filled_lots={parsed.filled_lots} "
              f"fill_fee={getattr(parsed, 'fill_fee', 'not parsed')}")


def _report_executions(adapter: KrakenAdapter, legs: List[Dict[str, Any]],
                       window_start: int) -> None:
    """
    Print each leg's executions with the venue's maker flag, then the ledger lines they made.

    Args:
        adapter: Live adapter
        legs: The legs that executed
        window_start: Unix second the probe began, the ledger read's lower edge
    """
    trade_ids = [tid for leg in legs for tid in (leg['reads'][-1]['trades'] if leg['reads'] else [])]
    if trade_ids:
        trades = adapter._fetch_private('/0/private/QueryTrades', {'txid': ','.join(trade_ids)})
        print('\nexecutions (QueryTrades)')
        for tid, trade in trades.items():
            print(f"  {tid} order={trade.get('ordertxid')} {trade.get('type'):4} "
                  f"vol={trade.get('vol')} cost={trade.get('cost')} fee={trade.get('fee')} "
                  f"maker={trade.get('maker')}")
    ledger = adapter._fetch_private('/0/private/Ledgers', {'start': window_start - 5})
    rows = sorted((ledger.get('ledger') or {}).values(), key=lambda entry: entry.get('time', 0))
    print('\nledger (the account debits)')
    for entry in rows:
        if entry.get('refid') in trade_ids:
            print(f"  {entry.get('refid')} {entry.get('asset'):5} amount={entry.get('amount'):>13} "
                  f"fee={entry.get('fee'):>12}")


def main() -> None:
    """Buy, read until settled, sell back, read until settled, then show executions and ledger."""
    adapter = _build_adapter()
    window_start = int(time.time())
    buy: Optional[Dict[str, Any]] = None
    sell: Optional[Dict[str, Any]] = None
    buy_txid: Optional[str] = None
    try:
        sent_at = time.monotonic()
        buy_txid = _send(adapter, 'buy', _LOTS)
        if buy_txid is None:
            print('AddOrder named no reference — nothing to follow or undo; check the account '
                  'by hand')
            return
        buy = _leg(adapter, 'buy', buy_txid, sent_at)
        _report_leg(buy)
    finally:
        held = _executed_volume(adapter, buy_txid) if buy_txid else 0.0
        if held > 0.0:
            sent_at = time.monotonic()
            sell_txid = _send(adapter, 'sell', f'{held:.8f}')
            if sell_txid is None:
                print(f'❌ the sell of {held:.8f} named no reference — check the account by hand')
            else:
                sell = _leg(adapter, 'sell', sell_txid, sent_at)
                _report_leg(sell)
                print(f'\nundone: sold {held:.8f} back ({sell_txid})')
    legs = [leg for leg in (buy, sell) if _executed(leg)]
    if legs:
        _report_executions(adapter, legs, window_start)


if __name__ == '__main__':
    main()
