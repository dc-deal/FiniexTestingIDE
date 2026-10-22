"""
The boot's broker-config line shows the session symbol's OWN specification age (#608).

The runtime cache file is rewritten by any symbol's refresh, so the file's age once read 0.0 days
beside an entry fetched almost a week earlier. The line now reads the symbol's `_last_fetched`.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from python.framework.autotrader.startup.autotrader_broker_config_setup import _log_broker_config_loaded
from python.framework.factory.broker_config_factory import BrokerConfigFactory

_SEED = Path('configs/brokers/kraken/kraken_spot_broker_config.json')


def _broker_config_with_eth_stamp(stamp):
    """
    The Kraken seed with ETHUSD's fetch stamp replaced.

    Args:
        stamp: The `_last_fetched` value to set, or None to remove it

    Returns:
        The built BrokerConfig
    """
    raw = json.loads(_SEED.read_text(encoding='utf-8'))
    entry = raw['symbols']['ETHUSD']
    entry.pop('_last_fetched', None)
    if stamp is not None:
        entry['_last_fetched'] = stamp
    # The seed's path tells the factory which broker it is
    return BrokerConfigFactory.build_from_dict(raw, source=str(_SEED))


class TestTheBootShowsTheSymbolsOwnAge:
    """The age beside the source is the session symbol's entry, never the file's."""

    def test_the_line_names_the_symbol_and_its_age(self, capsys):
        stamp = (datetime.now(timezone.utc) - timedelta(days=6.9)).strftime('%Y-%m-%dT%H:%M:%SZ')
        _log_broker_config_loaded(_broker_config_with_eth_stamp(stamp), 'cache.json', 'ETHUSD', MagicMock())
        assert '[ETHUSD spec fetched 6.9d ago]' in capsys.readouterr().out

    def test_an_entry_without_a_stamp_shows_no_age(self, capsys):
        _log_broker_config_loaded(_broker_config_with_eth_stamp(None), 'cache.json', 'ETHUSD', MagicMock())
        assert 'spec fetched' not in capsys.readouterr().out
