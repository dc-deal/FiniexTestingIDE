"""
FiniexTestingIDE - Broker Config Fetcher Factory
Central registry for broker-specific config fetcher instantiation.
"""

from typing import Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.configuration.autotrader.abstract_broker_config_fetcher import AbstractBrokerConfigFetcher
from python.configuration.autotrader.kraken_config_fetcher import KrakenConfigFetcher
from python.framework.logging.scenario_logger import ScenarioLogger


class BrokerConfigFetcherFactory:
    """
    Creates the correct AbstractBrokerConfigFetcher for a given broker type.

    Credentials and connection settings are read from market_config.json via
    MarketConfigManager — no credentials passing required at the call site.

    Single point of change when adding a new dynamic broker fetcher.
    """

    @staticmethod
    def create(
        broker_type: str,
        logger: Optional[ScenarioLogger] = None,
    ) -> AbstractBrokerConfigFetcher:
        """
        Instantiate the fetcher for a broker type.

        Args:
            broker_type: Broker type identifier (e.g., 'kraken_spot')
            logger: Optional logger for fetch progress output

        Returns:
            Fetcher instance for the broker

        Raises:
            NotImplementedError: If no fetcher is implemented for broker_type
        """
        if broker_type == 'kraken_spot':
            entry = MarketConfigManager().get_broker_entry(broker_type)
            return KrakenConfigFetcher(
                credentials_path=entry.credentials_file,
                logger=logger,
                api_base_url=entry.broker_transport.api_base_url or None,
            )

        raise NotImplementedError(
            f"❌ No config fetcher implemented for broker_type '{broker_type}'.\n"
            f"   The broker is declared config_mode=dynamic in market_config.json\n"
            f"   but has no corresponding fetcher class.\n"
            f"   Add a fetcher and register it in BrokerConfigFetcherFactory.create()."
        )
