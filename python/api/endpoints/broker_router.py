"""
Broker and symbol endpoints.

GET /api/v1/brokers/{broker}/symbols
"""

from fastapi import APIRouter

from python.api.api_route_documents import describes
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.api.api_error_catalog import (
    BROKER_NOT_FOUND,
    MARKET_TYPE_NOT_CONFIGURED,
    api_error,
)
from python.framework.types.api.api_types import SymbolInfo, SymbolListResponse

router = APIRouter()


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

    return SymbolListResponse(
        symbols=[SymbolInfo(symbol=s, market_type=market_type) for s in symbols]
    )
