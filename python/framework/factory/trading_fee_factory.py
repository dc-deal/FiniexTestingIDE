"""
FiniexTestingIDE - Trading Fee Factory
Factory functions for creating fee objects from order parameters

Creates appropriate fee objects based on broker type and order context.
"""

from python.framework.types.market_data_types import TickData
from python.framework.trading_env.trading_fees import (
    SpreadFee,
    MakerTakerFee
)


def create_spread_fee_from_tick(
    tick: TickData,
    lots: float,
    tick_value: float = 1.0,
    digits: int = 5
) -> SpreadFee:
    """
    Create SpreadFee from live tick data.

    Use in TradeSimulator when executing orders on spread-based brokers (MT5).

    Args:
        tick: Current tick data with bid/ask
        lots: Order size
        tick_value: Value per tick per lot
        digits: Symbol decimal places

    Returns:
        SpreadFee instance with calculated cost
    """
    return SpreadFee(
        bid=tick.bid,
        ask=tick.ask,
        lots=lots,
        tick_value=tick_value,
        digits=digits
    )


def create_maker_taker_fee(
    lots: float,
    contract_size: float,
    entry_price: float,
    maker_rate: float,
    taker_rate: float,
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
        is_maker: True if limit order that adds liquidity

    Returns:
        MakerTakerFee instance with calculated cost
    """
    order_value = lots * contract_size * entry_price

    return MakerTakerFee(
        is_maker=is_maker,
        maker_rate=maker_rate,
        taker_rate=taker_rate,
        order_value=order_value
    )
