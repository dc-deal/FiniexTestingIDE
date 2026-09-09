"""Probe: what did Kraken actually CHARGE for a run, against what we BOOKED?

The question belongs to #506. Its claim is that a maker/taker round trip is under-booked by
exactly one fee, and its acceptance is a comparison against the venue's own numbers rather
than against our estimate of them. This probe is that comparison, and it is the instrument to
re-run after the fix: the same run's references, asked again, must come back level.

It reads three things and puts them side by side, per order:
  1. what our run RECORDED   — the `fee` column of the run's events.csv
  2. what the venue CHARGED  — QueryOrders(txid).fee, the account's real charge
  3. the venue's own MAKER flag and ordertype, so a rate question and a classification
     question cannot be confused for one another

READ ONLY. QueryOrders is a private read: it places nothing and costs no fee, one rate-limit
unit per batch. Subjects are harvested from `runs/live/` — no argument, no fixture.

Recorded results:
  2026-09-08 — run 20260908_070032 (Kraken spot ETHUSD, real money). Every CLOSE leg recorded
      0.00000000 while the venue charged the same as the entry leg: the missing exit fee, in
      the venue's own numbers. AND the venue's rates were DOUBLE the declared ones — measured
      via TradeVolume for XETHZUSD: taker 0.8000 % / maker 0.4000 % against `fee_structure`
      declaring 0.40 / 0.25. The two defects multiply: the booking was a QUARTER of the charge.
      The venue reported `maker=True` for the limit entry it filled as a maker, so the earlier
      reading of that gap as a crossing-limit misclassification (#244) was wrong — it was the
      rate alone.

It reaches the adapter's Tier-3 transport (`_fetch_private`) on purpose: the point is the
venue's own figure, and the order-level `fee` is what the account was actually debited.

Usage:
    python python/experiments/venue_probes/probe_kraken_charged_vs_booked.py [<run dir>]
    (no argument = the newest run under runs/live/ that carries Kraken references)
"""

import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter

_ROOT = Path(__file__).resolve().parents[3]
_BROKER_CONFIG = _ROOT / 'configs/brokers/kraken/kraken_spot_broker_config.json'
_RUNS_DIR = _ROOT / 'runs/live'

_TXID_PATTERN = re.compile(r'O[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{6}')
_BATCH_SIZE = 20


def _pick_run(argv: list) -> Path:
    """Resolve the run directory to read.

    Args:
        argv: Command-line arguments; argv[1] is an explicit run directory when given

    Returns:
        The run directory, or None when nothing suitable was found
    """
    if len(argv) > 1:
        return Path(argv[1])
    candidates = [
        p.parent for p in _RUNS_DIR.rglob('events.csv')
        if _TXID_PATTERN.search(p.read_text(errors='ignore'))
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: (p / 'events.csv').stat().st_mtime)


def _booked_legs(run_dir: Path) -> list:
    """Read every fill the run recorded, with the fee it booked.

    Args:
        run_dir: A live run directory containing events.csv

    Returns:
        List of dicts with order_id, broker_ref, leg, volume, price and booked fee
    """
    legs = []
    seen_open = set()
    with open(run_dir / 'events.csv', newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            if row.get('event_type') != 'FILL':
                continue
            ref = (row.get('broker_ref') or '').strip()
            if not _TXID_PATTERN.fullmatch(ref):
                continue
            order_id = row.get('order_id') or ''
            leg = 'exit' if order_id in seen_open else 'entry'
            seen_open.add(order_id)
            legs.append({
                'order_id': order_id,
                'broker_ref': ref,
                'leg': leg,
                'volume': float(row.get('lots') or 0.0),
                'price': float(row.get('price') or 0.0),
                'booked_fee': float(row.get('fee') or 0.0),
                'booked_maker': (row.get('is_maker') or '').strip().lower() == 'true',
            })
    return legs


def _build_adapter() -> KrakenAdapter:
    """Build a live-enabled Kraken adapter for private reads only.

    Returns:
        KrakenAdapter with Tier-3 enabled and dry_run off
    """
    broker_config = json.loads(_BROKER_CONFIG.read_text())
    # The LIVE source, not a mirror: `configs/broker_settings/` was a leftover that
    # production stopped reading, so a probe against a real account could have run with a
    # credentials file or a transport the session itself no longer uses.
    entry = MarketConfigManager().get_broker_entry('kraken_spot')
    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=entry.credentials_file,
        dry_run=False,
        transport=entry.broker_transport,
    )
    return adapter


def _charged(adapter: KrakenAdapter, refs: list) -> dict:
    """Ask the venue what it charged for each reference.

    Args:
        adapter: Live-enabled adapter
        refs: Order references to ask about

    Returns:
        Reference → {fee, vol_exec, price, status}, missing where the venue named no order
    """
    out = {}
    for start in range(0, len(refs), _BATCH_SIZE):
        batch = refs[start:start + _BATCH_SIZE]
        raw = adapter._fetch_private(
            '/0/private/QueryOrders', {'txid': ','.join(batch), 'trades': 'false'})
        for ref in batch:
            info = raw.get(ref)
            if info is None:
                continue
            out[ref] = {
                'fee': float(info.get('fee', 0) or 0),
                'vol_exec': float(info.get('vol_exec', 0) or 0),
                'price': float(info.get('price', 0) or 0),
                'status': info.get('status'),
            }
    return out


def _declared_rates() -> tuple:
    """Read the rates the broker config declares.

    Returns:
        (maker_percent, taker_percent)
    """
    fee = json.loads(_BROKER_CONFIG.read_text()).get('fee_structure', {})
    return float(fee.get('maker_fee', 0.0)), float(fee.get('taker_fee', 0.0))


def _real_rates(adapter: KrakenAdapter, pair: str) -> tuple:
    """Ask the account for the fee schedule it is actually on.

    Args:
        adapter: Live-enabled adapter
        pair: The pair to ask about

    Returns:
        (maker_percent, taker_percent), or (None, None) when the venue did not answer
    """
    raw = adapter._fetch_private('/0/private/TradeVolume', {'pair': pair})
    taker = next(iter((raw.get('fees') or {}).values()), None)
    maker = next(iter((raw.get('fees_maker') or {}).values()), None)
    if taker is None or maker is None:
        return None, None
    return float(maker['fee']), float(taker['fee'])


def main() -> None:
    """Compare what the run booked against what the venue charged."""
    run_dir = _pick_run(sys.argv)
    if run_dir is None or not (run_dir / 'events.csv').exists():
        print(f'no live run with Kraken references found under {_RUNS_DIR}')
        return

    legs = _booked_legs(run_dir)
    if not legs:
        print(f'{run_dir} carries no Kraken-referenced fills')
        return

    adapter = _build_adapter()
    charged = _charged(adapter, sorted({leg['broker_ref'] for leg in legs}))

    declared_maker, declared_taker = _declared_rates()
    real_maker, real_taker = _real_rates(adapter, 'ETHUSD')
    print(f'run: {run_dir.name}   legs: {len(legs)}')
    print(f'declared rates : maker {declared_maker:.4f} %   taker {declared_taker:.4f} %')
    if real_maker is not None:
        print(f'account is on  : maker {real_maker:.4f} %   taker {real_taker:.4f} %')
        if abs(real_taker - declared_taker) > 1e-9 or abs(real_maker - declared_maker) > 1e-9:
            print('  ^ THE DECLARED RATES DO NOT MATCH THE ACCOUNT. Every estimate is off by '
                  'this factor before any leg is counted (#337).')
    print('=' * 104)
    print(f'{"order":18} {"leg":6} {"ref":22} {"booked":>11} {"charged":>11} {"diff":>11}  charged %')

    booked_total = 0.0
    charged_total = 0.0
    unanswered = []
    for leg in sorted(legs, key=lambda item: (item['order_id'], item['leg'])):
        info = charged.get(leg['broker_ref'])
        if info is None:
            unanswered.append(leg['broker_ref'])
            continue
        value = info['vol_exec'] * info['price']
        pct = (info['fee'] / value * 100) if value else 0.0
        booked_total += leg['booked_fee']
        charged_total += info['fee']
        print(f'{leg["order_id"]:18} {leg["leg"]:6} {leg["broker_ref"]:22} '
              f'{leg["booked_fee"]:11.8f} {info["fee"]:11.8f} '
              f'{info["fee"] - leg["booked_fee"]:11.8f}  {pct:.4f} %')

    print('=' * 104)
    print(f'{"TOTAL":18} {"":6} {"":22} {booked_total:11.8f} {charged_total:11.8f} '
          f'{charged_total - booked_total:11.8f}')
    if booked_total > 0:
        print(f'\nthe venue charged {charged_total / booked_total:.3f} x what the run booked')
    zero_exits = [leg for leg in legs if leg['leg'] == 'exit' and leg['booked_fee'] == 0.0]
    if zero_exits:
        print(f'{len(zero_exits)} exit leg(s) booked ZERO fee — the #506 defect, in the '
              f'venue\'s own numbers')
    if unanswered:
        print(f'\n{len(unanswered)} reference(s) the venue no longer describes: '
              f'{", ".join(unanswered)}')


if __name__ == '__main__':
    main()
