"""
Carry the contract-20 order-history shape into the runs recorded before it — exactly, or not at all.

A single-use migration, not a permanent code path: nothing imports it. Contract 20 serves the
order history's closed vocabularies as enums and an absent value as null, and a stored artifact
written before it holds empty strings and zeros instead — which the new model refuses to read, so
the API would answer every such run with an error until it is carried over.

What changes, row by row, and each value comes from the run's own records:

- `execution_time` is renamed `event_time`: on these rows it is the moment the row's event
  happened (a fill, a refusal, an expiry), where every other `execution_time` in the API is how
  long something ran;
- an empty `position_id`, `event_time`, `rejection_message`, `direction`, `action` or
  `rejection_reason` becomes null; a zero `requested_lots`, `executed_lots` or `executed_price`
  becomes null — the same mapping the builder applies to a new run;
- a REJECTED row gets the two facts its record never carried and the run can still answer:
  its `symbol` — the symbol of its unit, from the run's own portfolio artifact (a unit is one
  symbol) — and its `action`: `close` for a `close_` id, `open` for a `guard_` id, and for any
  other id `close` when the same unit executed an open under that id (only a close can be
  refused for an order that already opened a position), `open` otherwise.

NOT back-filled, because the records cannot answer it: a rejection's direction, its requested
size and the moment it was refused. Those stay null on the runs recorded before contract 20.

A run whose rejected row has no unit symbol to take is refused and named, and so is any run whose
carried-over artifact the model does not accept. The CSV beside each artifact is rewritten from
the carried-over report, so the two stay one table. Whether a run still needs it is decided on the
RAW keys, not by the model: the model ignores a field it does not know, so an artifact still
holding `execution_time` would read as current and lose the value on the next write.

Usage:
    python python/experiments/backfill_order_history_nulls.py --preview
    python python/experiments/backfill_order_history_nulls.py
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
from pydantic import ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.artifact_specs import ORDER_HISTORY_ARTIFACT
from python.framework.reporting.io.report_artifact_io import write_artifact
from python.framework.reporting.io.report_csv_io import write_order_history_csv
from python.framework.types.api.report_types import OrderHistoryReport
from python.framework.types.log_layout_types import IO_SUBDIR

# The served field's old name and its new one.
_RENAMED = ('execution_time', 'event_time')
# Empty string meant "absent" on these fields; contract 20 says null.
_EMPTY_TO_NULL = ('position_id', 'event_time', 'rejection_message', 'direction', 'action',
                  'rejection_reason')
# A zero meant "absent" on these; a zero size or price is a value downstream.
_ZERO_TO_NULL = ('requested_lots', 'executed_lots', 'executed_price')


def _unit_symbols(io_dir: Path) -> Dict[str, str]:
    """
    Each unit's symbol, from the run's own portfolio artifact.

    Args:
        io_dir: The run's io/ directory

    Returns:
        unit name → symbol; empty when the run has no portfolio artifact
    """
    path = io_dir / 'portfolio.json'
    if not path.exists():
        return {}
    units = json.loads(path.read_text(encoding='utf-8')).get('units', [])
    return {unit['name']: unit['symbol'] for unit in units if unit.get('symbol')}


def _rejected_side(order_id: str, opened: Set[Tuple[str, str]], unit: str) -> str:
    """
    Which side a stored rejection refused, derived from its id and its unit's own records.

    Args:
        order_id: The rejected row's order id
        opened: (unit, order id) of every executed open in the run
        unit: The rejected row's unit

    Returns:
        'close' or 'open'
    """
    if order_id.startswith('close_'):
        return 'close'
    if order_id.startswith('guard_'):
        return 'open'
    return 'close' if (unit, order_id) in opened else 'open'


def _carried_over(raw: Dict, symbols: Dict[str, str]) -> Tuple[Dict, Optional[str], Dict[str, int]]:
    """
    The stored report in the contract-20 shape.

    Args:
        raw: The stored artifact as parsed JSON
        symbols: unit name → symbol

    Returns:
        (the carried-over JSON, the reason the run is refused or None, counts of what changed)
    """
    rows: List[Dict] = raw.get('orders', [])
    opened = {(row.get('scenario_name', ''), row['order_id']) for row in rows
              if row.get('action') == 'open' and row.get('status') == 'executed'}
    counts = {'rows': len(rows), 'symbol_filled': 0, 'action_filled': 0}
    for row in rows:
        old, new = _RENAMED
        if old in row:
            row[new] = row.pop(old)
        unit = row.get('scenario_name', '')
        if row.get('status') == 'rejected':
            if not row.get('symbol'):
                symbol = symbols.get(unit)
                if not symbol:
                    return raw, f'rejected row {row["order_id"]} in unit {unit!r} has no unit symbol', counts
                row['symbol'] = symbol
                counts['symbol_filled'] += 1
            if not row.get('action'):
                row['action'] = _rejected_side(row['order_id'], opened, unit)
                counts['action_filled'] += 1
        for field in _EMPTY_TO_NULL:
            if row.get(field) == '':
                row[field] = None
        for field in _ZERO_TO_NULL:
            if row.get(field) == 0.0:
                row[field] = None
    raw['symbols'] = sorted({row['symbol'] for row in rows if row.get('symbol')})
    return raw, None, counts


def _is_current(raw: Dict) -> bool:
    """
    Whether a stored artifact already has the contract-20 shape.

    Args:
        raw: The stored artifact as parsed JSON

    Returns:
        True when no row carries the old field name and the model accepts it
    """
    if any(_RENAMED[0] in row for row in raw.get('orders', [])):
        return False
    try:
        OrderHistoryReport.model_validate(raw)
    except ValidationError:
        return False
    return True


def main() -> None:
    """Carry every stored order-history artifact over, or name why a run was left as it was."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument('--preview', action='store_true', help='report only, write nothing')
    args = parser.parse_args()

    index = pd.read_parquet(Path(AppConfigManager().get_file_logging_config_object().run_index))
    carried, untouched, refused = 0, 0, []
    totals = {'rows': 0, 'symbol_filled': 0, 'action_filled': 0}
    for run_dir in sorted(index.run_dir):
        io_dir = Path(run_dir) / IO_SUBDIR
        path = io_dir / ORDER_HISTORY_ARTIFACT.filename
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding='utf-8'))
        if _is_current(raw):
            untouched += 1
            continue
        fixed, reason, counts = _carried_over(raw, _unit_symbols(io_dir))
        if reason is None:
            try:
                report = OrderHistoryReport.model_validate(fixed)
            except ValidationError as e:
                reason = f'the carried-over artifact is still refused: {e.errors()[0]["msg"]}'
        if reason is not None:
            refused.append((str(io_dir.parent), reason))
            continue
        for key in totals:
            totals[key] += counts[key]
        carried += 1
        if not args.preview:
            write_artifact(report, io_dir, ORDER_HISTORY_ARTIFACT)
            write_order_history_csv(report, io_dir)

    verb = 'would carry over' if args.preview else 'carried over'
    print(f'{verb}: {carried} run(s), {totals["rows"]} row(s) — symbol filled on '
          f'{totals["symbol_filled"]}, side filled on {totals["action_filled"]} rejection(s)')
    print(f'already in the contract-20 shape: {untouched} run(s)')
    for run, reason in refused:
        print(f'REFUSED {run}: {reason}')


if __name__ == '__main__':
    main()
