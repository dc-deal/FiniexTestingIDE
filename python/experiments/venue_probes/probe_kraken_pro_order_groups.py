"""Probe: how does an order placed in Kraken Pro with TP/SL, or as an OCO, appear in the Spot API? (#625)

#625 retires the venue stop this framework rebuilt for Kraken, because Kraken's Spot API offers no OCO,
no bracket and only ONE conditional close per order. Kraken Pro, the venue's own interface, offers all of
it for spot: a take profit and a stop loss on an order (since 2025-01-15, on a stop entry as well), and
OCO as an order type. The Exchange Trading Rules list them as "Spot Group Orders". No API page says
whether such a group is visible through the API, and two questions hang on it:

  Q1  does the matching engine hold the group — legs that know each other — so that the API could take
      it one day, and a native mapping would replace any rebuilding?
  Q2  what does a session see at its boot and in its reconciliation when a person has placed such an
      order in the same account: one order or several, and linked by which field?

WHAT IT COSTS
    Private READS only: OpenOrders, QueryOrders for every reference a field names, and BalanceEx for
    what the resting orders hold. It places nothing, cancels nothing and amends nothing. The orders it looks at are placed BY HAND in Kraken Pro
    beforehand, far enough from the market that nothing fills, and cancelled by hand afterwards.

    It reaches the adapter's Tier-3 transport (`_fetch_private`) on purpose: what is measured is the raw
    answer, and the parse layer flattens exactly the fields being looked for.

    Run it only while no session uses the same API key — two processes on one key share no nonce
    counter, and the call that loses the race may be the session's. `activity_cli.py` lists what runs.

USAGE
    python python/experiments/venue_probes/probe_kraken_pro_order_groups.py

MEASUREMENTS
    (append each run here, dated, newest last — a repeated measurement is a series, not a series of
     surprises)

  2026-10-25 — three groups placed in Kraken Pro on BTC/USD, 0.00006 each, far from the market:
    a limit buy 56000 with TP 70000 / SL 50000, a stop-loss buy 104000 with TP 120000 / SL 95000, and a
    buy OCO 56000 / 104000. OpenOrders returned eight orders.
    Q1 — the engine holds the groups, and the API READS them through fields no API page documents:
      group_id     one id per group (`G…-…-…`), shared by every leg
      group_type   `bracket` (the TP/SL on an entry, a stop entry included) · `oco`
      group_role   bracket: `primary` (the entry, status `open`) and `template` (each TP and SL leg,
                   status `pending` until the entry fills) · oco: `child` for both legs, both `open`
    Every leg carries `trigger: index` (the order form's default), all legs of a group the same opentm,
    `descr.close` is empty: this is not the conditional close. Whether the API can PLACE a group is not
    documented (changelog read to 2026-10-06 for spot) — the question #625 depends on.
    Q2 — our adapter maps `pending` and `open` alike to PENDING, so a template that is not live yet reads
    as a resting order; no leg carries our key, so all are foreign.
    Holds (BalanceEx): ZUSD hold_trade 15.8400 = 3.36 (limit buy) + 6.24 (untriggered buy stop) + 6.24
    (the OCO, held ONCE — its larger leg). An untriggered BUY stop holds the quote currency; the
    templates hold nothing.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter

_ROOT = Path(__file__).resolve().parents[3]
_BROKER_CONFIG = _ROOT / 'configs/brokers/kraken/kraken_spot_broker_config.json'

# Field names that could carry a link between two orders. Matched as substrings of every key the
# venue sends, so a field nobody documented still shows up.
_LINK_HINTS = ('link', 'parent', 'group', 'oco', 'contingen', 'close', 'condition', 'trigger')


def _build_adapter() -> KrakenAdapter:
    """
    Build a live-enabled Kraken adapter for private reads, from the source a session reads.

    Returns:
        KrakenAdapter with Tier-3 enabled
    """
    broker_config = json.loads(_BROKER_CONFIG.read_text())
    entry = MarketConfigManager().get_broker_entry('kraken_spot')
    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=entry.credentials_file,
        dry_run=False,
        transport=entry.broker_transport,
    )
    return adapter


def _call(adapter: KrakenAdapter, endpoint: str,
          data: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[str]]:
    """
    Make one private read and return the venue's answer or the venue's refusal.

    Args:
        adapter: Live-enabled adapter
        endpoint: Private REST path
        data: Payload

    Returns:
        (raw answer or None, Kraken's error text or None)
    """
    try:
        return adapter._fetch_private(endpoint, data), None
    except ConnectionError as exc:
        return None, str(exc)


def _keys_of(value: Any, prefix: str = '') -> Set[str]:
    """
    Collect every key path of a nested answer.

    Args:
        value: A dict, a list or a scalar from the venue's answer
        prefix: The path above this value

    Returns:
        The dotted key paths found below it
    """
    if not isinstance(value, dict):
        return set()
    found: Set[str] = set()
    for key, inner in value.items():
        path = f'{prefix}.{key}' if prefix else key
        found.add(path)
        found |= _keys_of(inner, path)
    return found


def _references_in(order: Dict[str, Any]) -> List[str]:
    """
    Find order references that one order's fields name, besides its own.

    Args:
        order: One order as the venue returned it

    Returns:
        Every value shaped like a Kraken order reference, in the order found
    """
    found: List[str] = []
    for value in json.dumps(order).split('"'):
        if len(value) == 19 and value.count('-') == 2 and value.startswith('O') and value not in found:
            found.append(value)
    return found


def _print_holds(adapter: KrakenAdapter) -> None:
    """
    Show what the resting orders hold, per asset, as the venue reports it.

    Args:
        adapter: Live-enabled adapter
    """
    balances, error = _call(adapter, '/0/private/BalanceEx', {})
    if error is not None:
        print(f'❌ BalanceEx refused: {error}')
        return
    print('\nHeld by open orders (BalanceEx hold_trade):')
    for asset, entry in sorted((balances or {}).items()):
        held = float(entry.get('hold_trade', 0) or 0)
        if held:
            print(f"   {asset}: hold_trade {entry.get('hold_trade')} of balance {entry.get('balance')}")


def main() -> None:
    """Read the open orders and show how a hand-placed group appears."""
    if os.environ.get('FINIEX_CONFIG_ISOLATION') not in (None, '', '0'):
        print('❌ FINIEX_CONFIG_ISOLATION is set — the run would read the tracked placeholder '
              'credentials and measure nothing while looking like a failed API.')
        return

    adapter = _build_adapter()
    answer, error = _call(adapter, '/0/private/OpenOrders', {'trades': False})
    if error is not None:
        print(f'❌ OpenOrders refused: {error}')
        return
    orders: Dict[str, Dict[str, Any]] = (answer or {}).get('open', {})
    print(f'OpenOrders: {len(orders)} order(s) resting\n')
    if not orders:
        print('Nothing rests — place the orders in Kraken Pro first, then run this again.')
        _print_holds(adapter)
        return

    all_keys: Set[str] = set()
    for txid, order in sorted(orders.items()):
        all_keys |= _keys_of(order)
        print(f'── {txid}')
        print(json.dumps(order, indent=2, sort_keys=True))
        named = [ref for ref in _references_in(order) if ref != txid]
        if named:
            print(f'   names other references: {named}')
        print()

    hinted = sorted(k for k in all_keys if any(h in k.lower() for h in _LINK_HINTS))
    print('Every key seen:', ', '.join(sorted(all_keys)))
    print('Keys that could carry a link:', ', '.join(hinted) or '—')

    _print_holds(adapter)

    named_elsewhere = sorted({ref for txid, order in orders.items()
                              for ref in _references_in(order) if ref != txid and ref not in orders})
    if not named_elsewhere:
        return
    details, error = _call(adapter, '/0/private/QueryOrders',
                           {'txid': ','.join(named_elsewhere), 'trades': False})
    print(f'\nReferences named but not resting: {named_elsewhere}')
    print(json.dumps(details, indent=2, sort_keys=True) if error is None else f'❌ QueryOrders: {error}')


if __name__ == '__main__':
    main()
