"""
FiniexTestingIDE - Store Catalog views (#486)

Every registered data store with its index state, the advisories about the dated declarations the
installation holds, and the result of an index rebuild. Formatting only: the rows come from
`StoreCatalog.status()` and the advisories from `validators/store_health_checks.py`, so what is
flagged is decided there and never here.
"""

from typing import Dict, List, Optional

from python.framework.store.store_descriptor import PURPOSE_MAX_LENGTH
from python.framework.types.store_types import StoreId, StoreStatus
from python.framework.types.validation_types import ValidationFinding
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS_BY_ID


def render_store_catalog(rows: List[StoreStatus], findings: List[ValidationFinding],
                         with_sizes: bool) -> None:
    """
    Every store: the table, how one entry is addressed, where each is explained, what is stale,
    the advisories, and the stated reasons.

    Args:
        rows: The catalog's status rows
        findings: The store health advisories
        with_sizes: Whether the rows carry measured sizes and the size column is shown
    """
    header = (f'  {"KIND":<11} {"STORE":<18} {"ROOT":<38} {"INDEX":<31} '
              f'{"ENTRIES":>8}' + (f' {"SIZE":>10}' if with_sizes else '')
              + f'  {"PURPOSE":<{PURPOSE_MAX_LENGTH}}')
    print('\n' + '=' * len(header))
    print(f'🗄️  Store Catalog — {len(rows)} registered store(s)')
    print('=' * len(header) + '\n')
    print(header)
    print('  ' + '-' * (len(header) - 2))
    for row in rows:
        print(_format_row(row, with_sizes))
    # The KEY is what a reader needs to ADDRESS one entry, and it does not fit the table — several
    # are a sentence rather than a column. Printed underneath rather than dropped: a catalog that
    # promises the key and does not show it is a false map.
    print('\n  How ONE entry is addressed:')
    for row in rows:
        print(f'      {row.store_id.value:<18} {row.key}')
    print('\n  Where each store is explained:')
    for row in rows:
        print(f'      {row.store_id.value:<18} {row.doc}')
    _render_staleness(rows)
    _render_findings(findings)
    _render_notes(rows)
    print()


def render_store_rebuilt(store_id: StoreId, count: int) -> None:
    """
    One rebuilt index.

    Args:
        store_id: The store whose index was rebuilt
        count: How many entries it now holds
    """
    print(f'  ✅ {store_id.value:<18} {count} entr(y/ies) indexed')


def render_store_rebuild_refused(store_id: StoreId, reason: str) -> None:
    """
    An index that could not be rebuilt.

    Args:
        store_id: The store asked for
        reason: Why the catalog refused
    """
    print(f'  ⚠️  {store_id.value}: {reason}')


def _render_staleness(rows: List[StoreStatus]) -> None:
    """
    Indexes behind their store — the ones to rebuild, and the ones that refresh themselves.

    Args:
        rows: The catalog rows
    """
    stale = [r for r in rows if r.stale_reason and not r.self_healing]
    healing = [r for r in rows if r.stale_reason and r.self_healing]
    if stale:
        print('\n  ⚠️  Stale index — rebuild before trusting it '
              '(`store_cli.py rebuild <store>`)')
        for row in stale:
            print(f'      {row.store_id.value:<18} {row.stale_reason}')
    if healing:
        print('\n  ↻  Behind, but the store refreshes it on its next read — nothing to do')
        for row in healing:
            print(f'      {row.store_id.value:<18} {row.stale_reason}')


def _render_findings(findings: List[ValidationFinding]) -> None:
    """
    The advisories, one block per check under the check's catalog title.

    Args:
        findings: The store health advisories, in the order they were produced
    """
    by_check: Dict[str, List[ValidationFinding]] = {}
    for finding in findings:
        by_check.setdefault(finding.check, []).append(finding)
    for check, group in by_check.items():
        print(f'\n  ⏰ {VALIDATION_CHECKS_BY_ID[check].title}')
        for finding in group:
            print(f'      {finding.scope:<20} {finding.message}')


def _render_notes(rows: List[StoreStatus]) -> None:
    """
    The stated reasons: why a store is SPECIAL, or why it deliberately has no index.

    Args:
        rows: The catalog rows
    """
    noted = [r for r in rows if r.note]
    if not noted:
        return
    print('\n  Notes')
    for row in noted:
        print(f'    {row.store_id.value:<18} {row.note}')


def _format_row(row: StoreStatus, with_sizes: bool) -> str:
    """
    One catalog line.

    Args:
        row: The store's status
        with_sizes: Whether the size column is shown

    Returns:
        The formatted line
    """
    index = row.index_name or '—'
    if row.stale_reason:
        index = f'{index}  {"↻ refreshes on read" if row.self_healing else "⚠ stale"}'
    entries = '—' if row.entries is None else str(row.entries)
    root = row.root if row.exists else f'{row.root} (absent)'
    line = (f'  {row.kind.value:<11} {row.store_id.value:<18} {root:<38} '
            f'{index:<31} {entries:>8}')
    if with_sizes:
        line += f' {_human_size(row.size_bytes):>10}'
    return f'{line}  {row.purpose}'


def _human_size(size_bytes: Optional[int]) -> str:
    """
    Bytes as a short human-readable string.

    Args:
        size_bytes: The size, or None when it was not measured

    Returns:
        A right-sized unit string, or an em dash when there is nothing to show
    """
    if size_bytes is None:
        return '—'
    value = float(size_bytes)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'
