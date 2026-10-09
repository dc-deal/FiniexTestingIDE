"""
FiniexTestingIDE - Fixture Catalog CLI (#576)

The runs a consumer pins, produced and checked by one command. `produce` makes an entry's runs in
the ordinary stores and records the production; the newest production that carried every property
is the entry's current fixture.

Usage:
    python python/cli/fixture_catalog_cli.py list
    python python/cli/fixture_catalog_cli.py produce report_coverage
    python python/cli/fixture_catalog_cli.py produce --all
    python python/cli/fixture_catalog_cli.py verify demo_deployment
"""

import argparse
import sys
import traceback
from typing import List

from python.framework.fixture_catalog.fixture_catalog import (
    FIXTURE_CATALOG,
    FIXTURE_CATALOG_BY_ID,
    entry_ids,
)
from python.framework.fixture_catalog.fixture_catalog_console import (
    render_catalog,
    render_production,
    render_production_start,
    render_verification,
)
from python.framework.fixture_catalog.fixture_producer import FixtureProducer, failed_properties
from python.framework.fixture_catalog.fixture_production_store import FixtureProductionStore


class FixtureCatalogCli:
    """Command-line interface for the fixture catalog."""

    def __init__(self):
        """Open the configured production record."""
        self._store = FixtureProductionStore(declared_entries=set(entry_ids()))

    def cmd_list(self) -> int:
        """
        Print every entry with its current production.

        Returns:
            Process exit code
        """
        render_catalog(list(FIXTURE_CATALOG), self._store.current(), self._store.get_path())
        return 0

    def cmd_produce(self, ids: List[str]) -> int:
        """
        Produce entries, check them and record each production.

        Args:
            ids: The entries to produce, in order

        Returns:
            Process exit code — 1 when any production did not carry every property
        """
        producer = FixtureProducer(store=self._store)
        all_verified = True
        for entry_id in ids:
            entry = FIXTURE_CATALOG_BY_ID[entry_id]
            previous = self._store.current().get(entry_id)
            render_production_start(entry)
            production = producer.produce(entry)
            render_production(entry, production, previous)
            all_verified = all_verified and production.verified
        return 0 if all_verified else 1

    def cmd_verify(self, entry_id: str) -> int:
        """
        Check whether an entry's current production still carries every property.

        Args:
            entry_id: The entry

        Returns:
            Process exit code — 1 when a property no longer holds or nothing is current
        """
        entry = FIXTURE_CATALOG_BY_ID[entry_id]
        production = self._store.current().get(entry_id)
        failed = (failed_properties(entry, production.run_ids, production.deployment_ids,
                                    production.sweep_ids) if production else [])
        render_verification(entry, production, failed)
        return 0 if production is not None and not failed else 1


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Fixture Catalog CLI (the runs a consumer pins)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    subparsers.add_parser('list', help='Every entry with its current production')

    produce_parser = subparsers.add_parser(
        'produce', help='Produce an entry, check it, record the production')
    target = produce_parser.add_mutually_exclusive_group(required=True)
    target.add_argument('entry', nargs='?', choices=entry_ids(), help='The entry to produce')
    target.add_argument('--all', action='store_true', help='Produce every entry, in order')

    verify_parser = subparsers.add_parser(
        'verify', help="Check an entry's current production again")
    verify_parser.add_argument('entry', choices=entry_ids(), help='The entry to check')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = FixtureCatalogCli()

    try:
        if args.command == 'list':
            sys.exit(cli.cmd_list())
        elif args.command == 'produce':
            sys.exit(cli.cmd_produce(entry_ids() if args.all else [args.entry]))
        elif args.command == 'verify':
            sys.exit(cli.cmd_verify(args.entry))

    except KeyboardInterrupt:
        print('\n\n⚠️  Interrupted by user')
        sys.exit(130)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
