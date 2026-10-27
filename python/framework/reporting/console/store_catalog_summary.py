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

# The root column's width when every root is shorter
_ROOT_MIN_WIDTH = 38


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
    # Wide enough for the longest root: a fixed width let one long path push its row's later
    # columns out of line with the header.
    root_width = max([_ROOT_MIN_WIDTH] + [len(_root_text(row)) for row in rows])
    header = (f'  {"KIND":<11} {"STORE":<20} {"ROOT":<{root_width}} {"INDEX":<31} '
              f'{"ENTRIES":>8}' + (f' {"SIZE":>10}' if with_sizes else '')
              + f'  {"PURPOSE":<{PURPOSE_MAX_LENGTH}}')
    print('\n' + '=' * len(header))
    print(f'🗄️  Store Catalog — {len(rows)} registered store(s)')
    print('=' * len(header) + '\n')
    print(header)
    print('  ' + '-' * (len(header) - 2))
    for row in rows:
        print(_format_row(row, with_sizes, root_width))
    # The KEY is what a reader needs to ADDRESS one entry, and it does not fit the table — several
    # are a sentence rather than a column. Printed underneath rather than dropped: a catalog that
    # promises the key and does not show it is a false map.
    print('\n  How ONE entry is addressed:')
    for row in rows:
        print(f'      {row.store_id.value:<20} {row.key}')
    print('\n  Where each store is explained:')
    for row in rows:
        print(f'      {row.store_id.value:<20} {row.doc}')
    _render_entry_members(rows)
    _render_staleness(rows)
    _render_findings(findings)
    _render_notes(rows)
    print()


def _render_entry_members(rows: List[StoreStatus]) -> None:
    """
    The stores whose entry is a directory, and the command that lists what one may hold.

    Args:
        rows: The catalog's status rows
    """
    declaring = [row for row in rows if row.entry_members]
    if not declaring:
        return
    print('\n  What ONE entry holds — python python/cli/store_cli.py entry-members <store>:')
    for row in declaring:
        print(f'      {row.store_id.value:<20} {row.entry_members} declared file(s)')


def render_store_rebuilt(store_id: StoreId, count: int) -> None:
    """
    One rebuilt index.

    Args:
        store_id: The store whose index was rebuilt
        count: How many entries it now holds
    """
    print(f'  ✅ {store_id.value:<20} {count} entr(y/ies) indexed')


def render_store_rebuild_skipped(store_id: StoreId, loses: str) -> None:
    """
    An index `rebuild --all` left alone because its rebuild loses information.

    Args:
        store_id: The store left out
        loses: What its rebuild cannot restore
    """
    print(f'  ↷  {store_id.value:<20} skipped — a rebuild loses {loses}. Rebuild it alone with '
          f'--accept-loss if that is intended')


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
    stale = [r for r in rows if r.stale_reason and not r.self_healing and not r.rebuild_loses]
    lossy = [r for r in rows if r.stale_reason and not r.self_healing and r.rebuild_loses]
    healing = [r for r in rows if r.stale_reason and r.self_healing]
    if stale:
        print('\n  ⚠️  Stale index — rebuild before trusting it '
              '(`store_cli.py rebuild <store>`)')
        for row in stale:
            print(f'      {row.store_id.value:<20} {row.stale_reason}')
    if lossy:
        print('\n  ⚠️  Stale index whose rebuild LOSES information — repair it rather than '
              'rebuilding it')
        for row in lossy:
            print(f'      {row.store_id.value:<20} {row.stale_reason}. A rebuild loses '
                  f'{row.rebuild_loses}')
    if healing:
        print('\n  ↻  Behind, but the store refreshes it on its next read — nothing to do')
        for row in healing:
            print(f'      {row.store_id.value:<20} {row.stale_reason}')


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
        print(f'    {row.store_id.value:<20} {row.note}')


def _root_text(row: StoreStatus) -> str:
    """
    The root as the table shows it, marked when it does not exist.

    Args:
        row: The store's status

    Returns:
        The root text
    """
    return row.root if row.exists else f'{row.root} (absent)'


def _format_row(row: StoreStatus, with_sizes: bool, root_width: int = _ROOT_MIN_WIDTH) -> str:
    """
    One catalog line.

    Args:
        row: The store's status
        with_sizes: Whether the size column is shown
        root_width: Width of the root column

    Returns:
        The formatted line
    """
    index = row.index_name or '—'
    if row.stale_reason:
        index = f'{index}  {"↻ refreshes on read" if row.self_healing else "⚠ stale"}'
    entries = '—' if row.entries is None else str(row.entries)
    root = _root_text(row)
    line = (f'  {row.kind.value:<11} {row.store_id.value:<20} {root:<{root_width}} '
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
