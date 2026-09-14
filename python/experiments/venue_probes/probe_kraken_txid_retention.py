"""Probe: how long does Kraken still describe an order that is already CLOSED?

The question belongs to #503. Its boot resolver asks the venue what became of a protective
order that may have fired while this bot was down, and it can only ask by the order's txid.
If Kraken stops answering for a txid after some period, the resolver has a blind window —
and the length of that window decides whether it needs the time-ranged `ClosedOrders` read
as a fallback.

The measurement is LONGITUDINAL by nature: one run answers "still known after N minutes",
and only repeating it over days finds the edge. So this is not a one-shot script — re-run it
and record the answer beside the previous ones.

READ ONLY. It places nothing, cancels nothing, amends nothing: QueryOrders is a private read.
It costs one rate-limit unit per batch of txids and no fee.

It harvests its own subjects from `runs/live/`, so it needs no argument and no fixture: every
live session leaves the venue's own references in its logs.

Two answers per run:
  1. retention  — how many of the harvested txids Kraken still describes, and how old they are
  2. the absence — what a txid the venue never minted reads back as, which is the case the
     resolver actually turns on

Recorded results:
  2026-09-08 08:33 UTC — 61 of 61 known (22 filled, 39 cancelled), aged 70-93 minutes, each
      with status, vol_exec and closetm. A txid Kraken never minted returns `{}`.

It reaches the adapter's Tier-3 transport (`_fetch_private`) on purpose: the point is what the
VENUE sends, and the parse layer flattens exactly the detail being measured. `parse_query_response`
is called beside it to show what production would make of the same answer.

Usage:
    python python/experiments/venue_probes/probe_kraken_txid_retention.py
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter

_ROOT = Path(__file__).resolve().parents[3]
_BROKER_CONFIG = _ROOT / 'configs/brokers/kraken/kraken_spot_broker_config.json'
_RUNS_DIR = _ROOT / 'runs/live'

# Kraken's own reference shape, as it appears in every live session log.
_TXID_PATTERN = re.compile(r'O[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{6}')

# A reference of Kraken's shape that Kraken never minted — the deciding case.
_NEVER_MINTED = 'OZZZZZ-ZZZZZ-ZZZZZZ'

# QueryOrders accepts a comma-separated list; kept well under the documented ceiling.
_BATCH_SIZE = 20


def _harvest_txids() -> list:
    """Collect every Kraken reference the live run logs carry.

    Returns:
        Sorted list of unique txid strings, empty when no live run has been recorded
    """
    found = set()
    if not _RUNS_DIR.is_dir():
        return []
    for path in _RUNS_DIR.rglob('*'):
        if not path.is_file() or path.suffix not in ('.log', '.csv', '.json', '.jsonl'):
            continue
        found.update(_TXID_PATTERN.findall(path.read_text(errors='ignore')))
    return sorted(found)


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


def _query(adapter: KrakenAdapter, txids: list) -> dict:
    """Ask Kraken about a batch of references.

    Args:
        adapter: Live-enabled adapter
        txids: References to ask about

    Returns:
        Kraken's raw QueryOrders result, keyed by txid
    """
    return adapter._fetch_private(
        '/0/private/QueryOrders', {'txid': ','.join(txids), 'trades': 'false'})


def _report_retention(adapter: KrakenAdapter, txids: list, now: datetime) -> None:
    """Ask about every harvested reference and print what came back.

    Args:
        adapter: Live-enabled adapter
        txids: References to ask about
        now: Probe instant, used for the age column
    """
    known, unknown = [], []
    for start in range(0, len(txids), _BATCH_SIZE):
        batch = txids[start:start + _BATCH_SIZE]
        raw = _query(adapter, batch)
        for txid in batch:
            info = raw.get(txid)
            if info is None:
                unknown.append(txid)
                continue
            opentm = info.get('opentm')
            age_min = (now.timestamp() - float(opentm)) / 60.0 if opentm else 0.0
            known.append((txid, info.get('status'),
                          float(info.get('vol_exec', 0) or 0), age_min,
                          info.get('closetm')))

    print(f'\n--- still described by Kraken: {len(known)} / {len(txids)}')
    print(f'{"txid":24} {"status":10} {"vol_exec":>10}  {"age (min)":>10}  closed')
    for txid, status, volume, age_min, closetm in sorted(
            known, key=lambda row: row[3], reverse=True):
        closed = (datetime.fromtimestamp(float(closetm), timezone.utc).strftime('%H:%M:%S')
                  if closetm else '-')
        print(f'{txid:24} {status:10} {volume:10.5f}  {age_min:10.1f}  {closed}')

    print(f'\n--- no longer described: {len(unknown)}')
    for txid in unknown:
        print(f'  {txid}')
    if unknown:
        print('  ^ THIS is the retention edge. Record the age of the youngest one.')


def _report_the_absence(adapter: KrakenAdapter, now: datetime) -> None:
    """Ask about a reference the venue never minted and show both readings.

    Args:
        adapter: Live-enabled adapter
        now: Probe instant, passed to the parse layer
    """
    print(f'\n--- a reference Kraken never minted ({_NEVER_MINTED})')
    raw = _query(adapter, [_NEVER_MINTED])
    parsed = adapter.parse_query_response(raw, _NEVER_MINTED, now)
    print(f'  raw answer          : {json.dumps(raw)}')
    print(f'  key present         : {_NEVER_MINTED in raw}')
    print(f'  production reads it : {parsed.status.value} '
          f'(terminal={parsed.is_terminal}, filled_lots={parsed.filled_lots})')


def main() -> None:
    """Run both halves of the probe against the live account."""
    txids = _harvest_txids()
    if not txids:
        print(f'no Kraken references found under {_RUNS_DIR} — nothing to ask about')
        return

    adapter = _build_adapter()
    now = datetime.now(timezone.utc)
    print(f'probe at {now.isoformat()}  ·  {len(txids)} reference(s) harvested')
    print('=' * 100)
    _report_retention(adapter, txids, now)
    _report_the_absence(adapter, now)


if __name__ == '__main__':
    main()
