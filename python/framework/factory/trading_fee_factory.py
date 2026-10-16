"""
FiniexTestingIDE - Trading Fee Factory
Factory functions for creating fee objects from order parameters

Creates appropriate fee objects based on broker type and order context.
"""

from datetime import datetime

from python.framework.trading_env.trading_fees import MakerTakerFee


def create_maker_taker_fee(
    lots: float,
    contract_size: float,
    entry_price: float,
    maker_rate: float,
    taker_rate: float,
    timestamp: datetime,
    is_maker: bool = False
) -> MakerTakerFee:
    """
    Create MakerTakerFee from order parameters.

    Use in TradeSimulator when executing orders on crypto exchanges (Kraken).

    Args:
        lots: Order size
        contract_size: Contract size (usually 1 for crypto)
        entry_price: Entry price
        maker_rate: Maker fee percentage (e.g., 0.16)
        taker_rate: Taker fee percentage (e.g., 0.26)
        timestamp: When the fee is charged — the executor's canonical clock, never the
                   wall clock: the stamp travels into the position carry-over
        is_maker: True if limit order that adds liquidity

    Returns:
        MakerTakerFee instance with calculated cost
    """
    order_value = lots * contract_size * entry_price

    return MakerTakerFee(
        is_maker=is_maker,
        maker_rate=maker_rate,
        taker_rate=taker_rate,
        order_value=order_value,
        timestamp=timestamp
    )
