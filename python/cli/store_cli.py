"""
FiniexTestingIDE - Store CLI
The overview across every registered data store, and the rebuild handle for their indexes.

Usage:
    python python/cli/store_cli.py catalog [--sizes]
    python python/cli/store_cli.py rebuild <store>
    python python/cli/store_cli.py rebuild --all
"""

import argparse
import sys
from typing import List, Optional

from python.framework.exceptions.store_errors import StoreCatalogError
from python.framework.reporting.certificates.certificate_index import CertificateIndex
from python.framework.store.store_registrations import CERTIFICATES_ROOT
from python.framework.store.store_catalog import StoreCatalog
from python.framework.types.store_types import StoreId, StoreStatus


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


class StoreCli:
    """Command-line interface for the store catalog."""

    def __init__(self):
        self._catalog = StoreCatalog()

    def cmd_catalog(self, with_sizes: bool) -> int:
        """
        Print every registered store: kind, root, key, index state and entry count.

        Args:
            with_sizes: Also measure bytes on disk (a full walk per store)

        Returns:
            Process exit code
        """
        rows = self._catalog.status(with_sizes=with_sizes)
        print('\n' + '=' * 112)
        print(f'🗄️  Store Catalog — {len(rows)} registered store(s)')
        print('=' * 112 + '\n')
        header = (f'  {"KIND":<11} {"STORE":<18} {"ROOT":<38} {"INDEX":<31} '
                  f'{"ENTRIES":>8}' + (f' {"SIZE":>10}' if with_sizes else ''))
        print(header)
        print('  ' + '-' * (len(header) - 2))
        for row in rows:
            print(self._format_row(row, with_sizes))
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
        self._print_expired_certificates()
        self._print_notes(rows)
        print()
        return 0

    def cmd_rebuild(self, store: Optional[str], rebuild_all: bool) -> int:
        """
        Rebuild one store's index, or every index this model owns.

        Args:
            store: The store id to rebuild; ignored when rebuild_all is set
            rebuild_all: Rebuild every store that carries an index of ours

        Returns:
            Process exit code
        """
        targets = ([d.store_id for d in self._catalog.all() if d.index_factory is not None]
                   if rebuild_all else [StoreId(store)])
        print()
        for store_id in targets:
            try:
                count = self._catalog.rebuild(store_id)
            except StoreCatalogError as e:
                print(f'  ⚠️  {store_id.value}: {e}')
                return 1
            print(f'  ✅ {store_id.value:<18} {count} entr(y/ies) indexed')
        print()
        return 0

    @staticmethod
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
        return line

    @staticmethod
    def _print_expired_certificates() -> None:
        """
        Release gates whose NEWEST certificate has expired.

        Only the newest per family: an old certificate expiring is what old certificates do, and
        listing them would make a release gate four permanent lines of noise. What a release
        asks is whether the certificate that WOULD be presented still holds.
        """
        expired = CertificateIndex(CERTIFICATES_ROOT).expired_families()
        if not expired:
            return
        print(f'\n  ⏰ {len(expired)} release gate(s) whose NEWEST certificate has expired')
        for family, version, until in expired:
            print(f'      {family:<20} {version:<8} valid until {until[:10]}')

    @staticmethod
    def _print_notes(rows: List[StoreStatus]) -> None:
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


def main() -> int:
    """
    Parse arguments and dispatch.

    Returns:
        Process exit code
    """
    parser = argparse.ArgumentParser(
        description='Store CLI — the catalog over every registered data store (#486)')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    catalog_parser = subparsers.add_parser(
        'catalog', help='Show every registered store, its index and what it holds')
    catalog_parser.add_argument(
        '--sizes', action='store_true', default=False,
        help='Also measure bytes on disk — one stat per entry, opt-in for that reason')

    rebuild_parser = subparsers.add_parser(
        'rebuild', help='Rebuild a store index from the store contents')
    rebuild_parser.add_argument(
        'store', nargs='?', choices=[s.value for s in StoreId],
        help='Which store to rebuild')
    rebuild_parser.add_argument(
        '--all', action='store_true', default=False, dest='rebuild_all',
        help='Rebuild every index this model owns')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cli = StoreCli()
    if args.command == 'catalog':
        return cli.cmd_catalog(args.sizes)
    if not args.store and not args.rebuild_all:
        rebuild_parser.error('give a store name, or --all')
    return cli.cmd_rebuild(args.store, args.rebuild_all)


if __name__ == '__main__':
    sys.exit(main())
