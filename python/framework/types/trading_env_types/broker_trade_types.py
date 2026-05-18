"""
FiniexTestingIDE - Broker Trade Record Types

Domain type for individual broker executions / fills / deals — the realization
of an order. One order produces 1..N BrokerTrade records (full or partial fills).
Broker-agnostic shape, mapped from Kraken QueryTrades, MT5 HistoryDealsGet, etc.

See docs/architecture/broker_trade_records.md for the order ↔ executions model.
"""

from dataclasses import dataclass
from datetime import datetime

from python.framework.types.trading_env_types.order_types import OrderDirection


@dataclass
class BrokerTrade:
    """
    A single execution / fill / deal — child of a broker order.

    Broker-agnostic. Kraken 'trade record' and MT5 'deal' both map to this
    shape. The parent order is referenced via parent_broker_ref. Our internal
    routing uses order_id (set at submit time) as primary routing key.

    Args:
        trade_id: Broker's execution ID (Kraken tradeid, MT5 deal ticket)
        parent_broker_ref: Parent order's broker_ref (Kraken txid, MT5 order ticket)
        order_id: OUR internal order_id — primary routing key in drain handlers
        volume: Lots filled in THIS execution
        price: Price of THIS execution
        fee: Broker-reported fee for THIS execution
        fee_currency: Fee currency (e.g. 'USD', 'EUR')
        timestamp: UTC timezone-aware execution time
        side: LONG / SHORT (matches parent order direction)
        is_maker: True for LIMIT/maker fills, False for taker (market)
    """
    trade_id: str
    parent_broker_ref: str
    order_id: str
    volume: float
    price: float
    fee: float
    fee_currency: str
    timestamp: datetime
    side: OrderDirection
    is_maker: bool

    def to_dict(self) -> dict:
        return {
            'trade_id': self.trade_id,
            'parent_broker_ref': self.parent_broker_ref,
            'order_id': self.order_id,
            'volume': self.volume,
            'price': self.price,
            'fee': self.fee,
            'fee_currency': self.fee_currency,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'side': self.side.value if self.side else None,
            'is_maker': self.is_maker,
        }
