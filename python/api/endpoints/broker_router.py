"""
Broker and symbol endpoints.

GET /api/v1/brokers
GET /api/v1/brokers/{broker}/symbols

On the `brokers` surface, the list of brokers included: which venues this installation holds data
for is something it holds, and nobody without a grant is shown that (contract 35).
"""

from typing import Optional

from fastapi import APIRouter

from python.api.api_route_documents import describes
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.api.api_error_catalog import (
    BROKER_NOT_FOUND,
    MARKET_TYPE_NOT_CONFIGURED,
    api_error,
)
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.api.api_types import BrokerListResponse, SymbolInfo, SymbolListResponse

router = APIRouter()


@router.get('/brokers', response_model=BrokerListResponse, openapi_extra=describes('market-data'))
def list_brokers() -> BrokerListResponse:
    """List every broker the bar index holds data for."""
    index = BarsIndexManager()
    index.load_index()
    return BrokerListResponse(brokers=index.list_broker_types())


@router.get('/brokers/{broker}/symbols', response_model=SymbolListResponse,
            openapi_extra=describes('market-data'))
def list_symbols(broker: str) -> SymbolListResponse:
    """List all symbols available for a broker, including market type."""
    index = BarsIndexManager()
    index.load_index()

    if broker not in index.list_broker_types():
        raise api_error(BROKER_NOT_FOUND, broker=broker)

    symbols = index.list_symbols(broker_type=broker)
    market_config = MarketConfigManager()

    try:
        market_type = market_config.get_market_type(broker).value
    except (ValueError, KeyError):
        raise api_error(MARKET_TYPE_NOT_CONFIGURED, broker=broker)

    broker_config = BrokerConfigFactory.build_broker_config(
        market_config.get_broker_config_path(broker))
    return SymbolListResponse(
        symbols=[SymbolInfo(symbol=s, market_type=market_type,
                            tick_size=_tick_size(broker_config, s)) for s in symbols]
    )


def _tick_size(broker_config: BrokerConfig, symbol: str) -> Optional[float]:
    """
    A symbol's smallest price step from the broker's configuration.

    Args:
        broker_config: The broker's configuration, read once per request
        symbol: The symbol

    Returns:
        The step, or None where the configuration names no such symbol — data can be held for a
        symbol the configuration no longer lists
    """
    try:
        return broker_config.get_symbol_specification(symbol).tick_size
    except ValueError:
        return None
