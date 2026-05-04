"""
Broker Config CLI — manage broker configuration runtime caches.

Commands:
    sync    Fetch all tick-index symbols for dynamic brokers and update the runtime cache.
"""
import argparse
from typing import List, Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.configuration.autotrader.broker_config_fetcher_factory import BrokerConfigFetcherFactory
from python.framework.types.market_types.market_config_types import ConfigMode


class BrokerConfigCli:
    """CLI for broker configuration management."""

    def __init__(self):
        self._parser = argparse.ArgumentParser(
            prog='broker_config_cli.py',
            description='Broker configuration management.',
        )
        subparsers = self._parser.add_subparsers(dest='command')

        sync_parser = subparsers.add_parser(
            'sync',
            help='Sync runtime cache from broker API for all tick-index symbols.',
        )
        sync_parser.add_argument(
            '--broker',
            type=str,
            default=None,
            help='Broker type to sync (default: all dynamic brokers)',
        )
    def run(self) -> None:
        args = self._parser.parse_args()
        if args.command == 'sync':
            self._cmd_sync(args)
        else:
            self._parser.print_help()

    def _cmd_sync(self, args) -> None:
        market_config = MarketConfigManager()

        if args.broker:
            broker_types = [args.broker]
        else:
            broker_types = [
                bt for bt in market_config.get_all_broker_types()
                if market_config.get_config_mode(bt) == ConfigMode.DYNAMIC
            ]

        if not broker_types:
            print('No dynamic brokers configured. Nothing to sync.')
            return

        for broker_type in broker_types:
            self._sync_broker(broker_type)

    def _sync_broker(self, broker_type: str) -> None:
        """
        Fetch all tick-index symbols for a broker and merge into the runtime cache.

        Args:
            broker_type: Broker type identifier
        """
        from python.data_management.index.tick_index_manager import TickIndexManager

        fetcher = BrokerConfigFetcherFactory.create(broker_type=broker_type)

        tick_index = TickIndexManager()
        tick_index.build_index()
        symbols = tick_index.list_symbols(broker_type)

        if not symbols:
            print(f'  ⚠️  {broker_type}: no symbols in tick index — nothing to sync')
            return

        print(f'\n  🔄  {broker_type}: syncing {len(symbols)} symbol(s) from tick index...')
        print(f'      Symbols: {symbols}')

        last_result = None
        for symbol in symbols:
            last_result = fetcher.fetch_broker_config_with_cache(symbol, broker_type)
            print(f'       ✓  {symbol}')

        if last_result:
            config_hash = last_result.get('_config_meta', {}).get('symbols_hash', '?')
            active_count = sum(
                1 for s in last_result.get('symbols', {}).values()
                if s.get('_active', True)
            )
            print(f'\n  ✅  {broker_type}: cache updated [{config_hash}] — {active_count} active symbols\n')


if __name__ == '__main__':
    BrokerConfigCli().run()
