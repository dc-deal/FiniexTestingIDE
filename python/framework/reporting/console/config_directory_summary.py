"""
FiniexTestingIDE - Config Directory views (#554)

Every configuration that can start a run, what one of them declares, and what the loader makes
of them. Formatting only: every figure comes off the directory model (§12), the same objects the
API serves.
"""

from typing import List, Optional

from python.framework.types.api.directory_types import (
    DirectoryDetailResponse,
    DirectoryListResponse,
    DirectoryRow,
)
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigReadStatus,
    ConfigValidationResult,
)

_RULE = '─' * 118
_KIND_TITLES = {ConfigKind.SCENARIO_SET: '📙 SCENARIO SETS',
                ConfigKind.AUTOTRADER_PROFILE: '🤖 AUTOTRADER PROFILES'}


def render_config_directory(listing: DirectoryListResponse, kind: Optional[ConfigKind] = None,
                            indent: str = '  ') -> None:
    """
    Every configuration file, grouped by kind, unreadable ones last with their reason.

    Args:
        listing: The directory
        kind: Only this kind; None shows both
        indent: Left padding
    """
    kinds = [kind] if kind else list(ConfigKind)
    # The file name is what `show` takes, so it is never cut: the column is as wide as the longest.
    file_width = max([len('file')] + [len(row.file) for row in listing.rows])
    rule = '─' * (file_width + 81)
    for shown in kinds:
        rows = [row for row in listing.rows if row.kind == shown]
        print(f'\n{indent}{_KIND_TITLES[shown]} — {len(rows)} file(s)')
        print(f'{indent}{rule}')
        print(f'{indent}{"file":<{file_width}} {"where":<24} {"scen":>7}  {"symbols":<22} '
              f'{"runs":>5}  {"last run":<16}')
        print(f'{indent}{rule}')
        for row in rows:
            _render_row(row, file_width, indent)
    unreadable = [row for row in listing.rows if row.status == ConfigReadStatus.UNREADABLE]
    if unreadable:
        print(f'\n{indent}⚠️  UNREADABLE — {len(unreadable)} file(s), shown so an edit in progress '
              f'is not mistaken for a file that is gone')
        print(f'{indent}{rule}')
        for row in unreadable:
            print(f'{indent}{row.file:<{file_width}} {_where(row):<24} {row.reason}')
    print()


def render_config_directory_entry(detail: DirectoryDetailResponse, indent: str = '  ') -> None:
    """
    One configuration file: what it declares, its scenarios and the runs started from it.

    Args:
        detail: The directory's detail for the file
        indent: Left padding
    """
    row = detail.row
    print(f'\n{indent}📄 {row.file}  ({row.kind or "unreadable"}, {_where(row)})')
    print(f'{indent}{_RULE}')
    if row.status == ConfigReadStatus.UNREADABLE:
        print(f'{indent}  unreadable: {row.reason}')
        return
    print(f'{indent}  name:        {row.name}')
    print(f'{indent}  changed:     {row.modified_at[:19]}')
    print(f'{indent}  logic:       {", ".join(row.decision_logics) or "—"}')
    print(f'{indent}  workers:     {", ".join(row.workers) or "—"}')
    print(f'{indent}  markets:     {", ".join(row.market_types) or "—"}  '
          f'({", ".join(row.broker_types) or "—"})')
    if row.kind == ConfigKind.AUTOTRADER_PROFILE:
        dry_run = {True: 'true', False: 'FALSE — real orders', None: 'broker default'}
        print(f'{indent}  bot:         {row.bot_id or "—"}  ·  adapter {row.adapter_type}  ·  '
              f'dry_run {dry_run[row.dry_run_declared]}')
    if row.shadowed:
        print(f'{indent}  shadows:     {", ".join(origin.value for origin in row.shadowed)} — a '
              f'same-named file there is NOT the one that runs')
    if detail.scenarios:
        print(f'\n{indent}  {"scenario":<34} {"symbol":<9} {"market":<8} {"start":<26} '
              f'{"end / ticks":<26} {"on":<3}')
        for scenario in detail.scenarios:
            window = scenario.end or (f'{scenario.max_ticks:,} ticks'
                                      if scenario.max_ticks else '—')
            print(f'{indent}  {scenario.name[:34]:<34} {scenario.symbol:<9} '
                  f'{scenario.market_type:<8} {scenario.start[:26]:<26} {window[:26]:<26} '
                  f'{"✓" if scenario.enabled else "–":<3}')
    print(f'\n{indent}  runs on record: {row.run_count}'
          + (f' — newest {row.last_run_id}' if row.last_run_id else ''))
    print()


def render_config_validation(results: List[ConfigValidationResult], indent: str = '  ') -> None:
    """
    What the loader makes of each scenario set — accepted with its scenario count, or its refusal.

    Args:
        results: One result per file
        indent: Left padding
    """
    failed = [result for result in results if not result.ok]
    print(f'\n{indent}🔎 SCENARIO SETS THROUGH THE LOADER — {len(results) - len(failed)} of '
          f'{len(results)} accepted')
    print(f'{indent}{_RULE}')
    for result in results:
        if result.ok:
            print(f'{indent}✅ {result.file:<50} {result.scenarios} scenario(s)')
        else:
            print(f'{indent}❌ {result.file:<50} {result.reason.splitlines()[0][:120]}')
    print(f'\n{indent}Parameter names are checked later, at the start of a batch — a set '
          f'accepted here can still be refused there.')
    print()


def _render_row(row: DirectoryRow, file_width: int, indent: str) -> None:
    """
    One readable row of the list.

    Args:
        row: The row
        file_width: Width of the file column — the longest file name in the listing
        indent: Left padding
    """
    scenarios = (f'{row.scenarios_enabled}/{row.scenarios_declared}'
                 if row.kind == ConfigKind.SCENARIO_SET else '1')
    symbols = ', '.join(row.symbols)
    symbols = symbols if len(symbols) <= 22 else symbols[:21] + '…'
    last_run = row.last_run_at[:16].replace('T', ' ') if row.last_run_at else '—'
    print(f'{indent}{row.file:<{file_width}} {_where(row)[:24]:<24} {scenarios:>7}  {symbols:<22} '
          f'{row.run_count:>5}  {last_run:<16}')


def _where(row: DirectoryRow) -> str:
    """
    A row's origin as the operator knows it — the sub-folder too, inside `configs/`.

    Args:
        row: The row

    Returns:
        `configs/backtesting`, `user_algos`, …
    """
    return f'{row.origin.value}/{row.folder}' if row.folder else row.origin.value
