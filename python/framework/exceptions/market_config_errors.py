"""
FiniexTestingIDE - Market Config Errors

TradingDayAnchorMissingError (#476): a market declares neither `trading_day_anchor` nor
`swap_rollover`, so nothing says where its trading day flips. Raised by
`MarketConfigManager.get_trading_day_anchor` rather than defaulting to midnight UTC —
that default is right for crypto and silently wrong for every quote-driven market, and a
record fragment sealed on the wrong boundary looks exactly like one sealed on the right one.
"""

from python.framework.exceptions.finiex_error import FiniexError


class TradingDayAnchorMissingError(FiniexError, ValueError):
    """A market's rules say nothing about where its trading day flips."""

    def __init__(self, broker_type: str, market_type: str):
        self.broker_type = broker_type
        self.market_type = market_type
        super().__init__(
            f"Broker '{broker_type}' trades market '{market_type}', whose market_rules "
            f"declare neither 'trading_day_anchor' nor 'swap_rollover'. Add one to "
            f"market_config.json under market_rules.{market_type} — a market without a "
            f"day boundary cannot rotate its log, reset a daily limit or seal a record "
            f"fragment."
        )
