"""
FiniexTestingIDE - Store CLI
The overview across every registered data store, and the rebuild handle for their indexes.

Parameter reception only: the rows come from `StoreCatalog`, the advisories from
`validators/store_health_checks.py`, and everything is rendered in
`reporting/console/store_catalog_summary.py`.

Usage:
    python python/cli/store_cli.py catalog [--sizes]
    python python/cli/store_cli.py rebuild <store> [--accept-loss]
    python python/cli/store_cli.py rebuild --all
"""

import argparse
import sys
from typing import Optional

from python.framework.exceptions.store_errors import StoreCatalogError
from python.framework.reporting.console.store_catalog_summary import (
    render_store_catalog,
    render_store_rebuild_refused,
    render_store_rebuild_skipped,
    render_store_rebuilt,
)
from python.framework.store.store_catalog import StoreCatalog
from python.framework.types.store_types import StoreId
from python.framework.validators.store_health_checks import check_store_health


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
        render_store_catalog(self._catalog.status(with_sizes=with_sizes), check_store_health(),
                             with_sizes)
        return 0

    def cmd_rebuild(self, store: Optional[str], rebuild_all: bool, accept_loss: bool) -> int:
        """
        Rebuild one store's index, or every index a rebuild fully restores.

        Args:
            store: The store id to rebuild; ignored when rebuild_all is set
            rebuild_all: Rebuild every store whose index of ours a rebuild fully restores
            accept_loss: Rebuild the one named store even though its rebuild loses information

        Returns:
            Process exit code
        """
        targets = self._catalog.lossless_rebuild_ids() if rebuild_all else [StoreId(store)]
        print()
        for store_id in targets:
            try:
                count = self._catalog.rebuild(store_id, accept_loss=accept_loss)
            except StoreCatalogError as e:
                render_store_rebuild_refused(store_id, str(e))
                return 1
            render_store_rebuilt(store_id, count)
        if rebuild_all:
            for store_id in self._catalog.lossy_rebuild_ids():
                render_store_rebuild_skipped(store_id, self._catalog.get(store_id).rebuild_loses)
        print()
        return 0


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
        help='Rebuild every index this model owns that a rebuild fully restores')
    rebuild_parser.add_argument(
        '--accept-loss', action='store_true', default=False, dest='accept_loss',
        help='Rebuild the named store even though its rebuild loses what only its index holds')

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cli = StoreCli()
    if args.command == 'catalog':
        return cli.cmd_catalog(args.sizes)
    if not args.store and not args.rebuild_all:
        rebuild_parser.error('give a store name, or --all')
    if args.accept_loss and args.rebuild_all:
        rebuild_parser.error('--accept-loss names one store; it does not combine with --all')
    return cli.cmd_rebuild(args.store, args.rebuild_all, args.accept_loss)


if __name__ == '__main__':
    sys.exit(main())
