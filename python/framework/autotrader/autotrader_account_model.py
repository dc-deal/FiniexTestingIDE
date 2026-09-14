"""
FiniexTestingIDE - AutoTrader Account Model

What the broker config and the profile together say about the account a live session trades:
which market, which trading model, and in which currency the books are kept.

A bundle rather than five return values, for the reason its sibling
`autotrader_pipeline_bundle.py` gives: neighbours of related type in a fixed-order tuple can
be swapped at the call site and nothing notices. Here two of the five are strings.

Lives beside its builder (`autotrader_startup.setup_pipeline`) rather than in
`framework/types/`: `SymbolSpecification` is data, but the model is a boot-time intermediate
of one unit, and the pipeline bundle established the pattern.
"""

from dataclasses import dataclass

from python.framework.types.config_types.market_config_types import MarketType, TradingModel
from python.framework.types.trading_env_types.broker_types import SymbolSpecification


@dataclass
class AutotraderAccountModel:
    """
    The resolved account for this session.

    Args:
        market_type: Which market the broker trades (FOREX, CRYPTO, …)
        trading_model: SPOT or MARGIN — decides how a position is held and closed
        symbol_spec: The traded symbol's specification, read once from the broker config
        account_currency: The currency balances and P&L are expressed in
    """
    market_type: MarketType
    trading_model: TradingModel
    symbol_spec: SymbolSpecification
    account_currency: str

    @property
    def spot_mode(self) -> bool:
        """
        Whether this account keeps an asset inventory rather than margin positions.

        Returns:
            True for SPOT, False for MARGIN
        """
        return self.trading_model == TradingModel.SPOT
