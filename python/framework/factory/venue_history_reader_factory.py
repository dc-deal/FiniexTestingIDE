"""
The seam to what a venue itself records: its public trade list and an account's ledger, by broker
type.

A caller names the broker type and receives a reader that speaks the venue-neutral contract, or
None where the venue has no such record or no reader for it — never a Kraken class. This module and
the tick source's setup are the framework's only places that name the Kraken package. When the
adapter segregation (#328) brings its adapter factory, the factory replaces the inside of these
functions and their callers do not change; the venue's public bars for the live chart (#655) take
their reader from beside them.
"""

from typing import Optional

from python.adapters.kraken.kraken_api_client import KrakenApiClient
from python.adapters.kraken.kraken_public_trade_reader import KrakenPublicTradeReader
from python.adapters.kraken.kraken_venue_ledger_reader import KrakenVenueLedgerReader
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)
from python.framework.venue_history.abstract_venue_ledger_reader import (
    AbstractVenueLedgerReader,
)


def public_trade_reader(broker_type: str, connection: ConnectionPolicy,
                        logger: AbstractLogger) -> Optional[AbstractPublicTradeReader]:
    """
    The reader of a venue's public trade list.

    Args:
        broker_type: The broker type ('kraken_spot')
        connection: The ladder's numbers for one request
        logger: Where the ladder reports

    Returns:
        The reader; None where the venue publishes no trade list we read — a forex broker quotes
        prices and publishes no trades
    """
    if BrokerType(broker_type) is BrokerType.KRAKEN_SPOT:
        return KrakenPublicTradeReader(KrakenApiClient(connection.request_timeout_s), connection,
                                       logger)
    return None


def venue_ledger_reader(broker_type: str, credentials_file: str, connection: ConnectionPolicy,
                        logger: AbstractLogger) -> Optional[AbstractVenueLedgerReader]:
    """
    The reader of an account's ledger at its venue.

    Args:
        broker_type: The broker type ('kraken_spot')
        credentials_file: The configured name of a key that may read the ledger and the closed
            orders and trades — never the trading key of a running bot, whose nonce a second
            reader would overtake
        connection: The ladder's numbers for one request
        logger: Where the ladder reports

    Returns:
        The reader; None where the venue's ledger has no reader yet
    """
    if BrokerType(broker_type) is BrokerType.KRAKEN_SPOT:
        return KrakenVenueLedgerReader(
            KrakenApiClient(connection.request_timeout_s, credentials_file=credentials_file),
            connection, logger)
    return None
