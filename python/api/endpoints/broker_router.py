"""
Broker and symbol endpoints.

GET /api/v1/brokers/{broker}/symbols
"""

from fastapi import APIRouter

from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.types.api.api_types import ApiException, SymbolInfo, SymbolListResponse

router = APIRouter()


@router.get('/brokers/{broker}/symbols', response_model=SymbolListResponse)
def list_symbols(broker: str) -> SymbolListResponse:
    """List all symbols available for a broker, including market type."""
    index = BarsIndexManager()
    index.load_index()

    if broker not in index.list_broker_types():
        raise ApiException(404, 'not_found', f"Broker '{broker}' not found in bar index.")

    symbols = index.list_symbols(broker_type=broker)
    market_config = MarketConfigManager()

    try:
        market_type = market_config.get_market_type(broker).value
    except (ValueError, KeyError):
        raise ApiException(500, 'config_error', f"No market_type configured for broker '{broker}'.")

    return SymbolListResponse(
        symbols=[SymbolInfo(symbol=s, market_type=market_type) for s in symbols]
    )
