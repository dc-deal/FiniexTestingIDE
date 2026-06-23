"""
FiniexTestingIDE - Market Clock
Swap-rollover + weekend market-awareness derived from the canonical clock (#365).
"""

from datetime import datetime, timedelta
from typing import Callable, Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.config_types.market_config_types import SwapRolloverConfig
from python.framework.utils.market_calendar import MarketCalendar
from python.framework.utils.time_utils import mt5_weekday_to_python


class MarketClock:
    """
    Market-awareness over the canonical clock.

    Composes the executor's canonical time source with MarketCalendar and the broker's
    market rules to answer rollover / weekend questions for decision logic. Deterministic
    (config + calendar math only) — safe for an algo to read at runtime.

    Single home for the swap-rollover config: the executor constructs ONE MarketClock,
    passes the resolved rollover to the PortfolioManager (overnight accrual) and exposes
    this clock for the DecisionTradingApi (which only forwards to it).
    """

    def __init__(self, clock_fn: Callable[[], datetime], broker_config: BrokerConfig):
        """
        Args:
            clock_fn: Canonical current-time source (executor.get_current_time)
            broker_config: Broker config — market resolution + per-symbol swap weekday
        """
        self._clock_fn = clock_fn
        self._broker_config = broker_config
        self._swap_rollover: Optional[SwapRolloverConfig] = None
        self._weekend_closure = False
        try:
            broker_type = broker_config.broker_type.value
            manager = MarketConfigManager()
            self._swap_rollover = manager.get_swap_rollover(broker_type)
            self._weekend_closure = manager.get_market_rules(
                manager.get_market_type(broker_type)).weekend_closure
        except (ValueError, KeyError, AttributeError):
            self._swap_rollover = None
            self._weekend_closure = False

    def get_swap_rollover(self) -> Optional[SwapRolloverConfig]:
        """The resolved swap-rollover anchor (None for swap-less markets like crypto spot)."""
        return self._swap_rollover

    def get_time_to_next_rollover(self) -> Optional[timedelta]:
        """
        Time from the current clock to the next swap rollover.

        Returns:
            timedelta to the next broker rollover, or None for markets without swap
        """
        if self._swap_rollover is None:
            return None
        now = self._clock_fn()
        next_rollover, _ = MarketCalendar.next_swap_rollover(
            now, self._swap_rollover.local_time, self._swap_rollover.timezone, 0)
        return next_rollover - now

    def is_next_rollover_triple(self, symbol: str) -> bool:
        """
        Whether the next swap rollover books triple swap (the broker's triple-swap weekday).

        Args:
            symbol: Symbol whose triple-swap weekday is read

        Returns:
            True if the next rollover is the triple-swap day; False for normal nights or
            markets without swap
        """
        if self._swap_rollover is None:
            return False
        spec = self._broker_config.get_symbol_specification(symbol)
        triple_weekday_py = mt5_weekday_to_python(spec.swap_rollover3days)
        now = self._clock_fn()
        _, multiplier = MarketCalendar.next_swap_rollover(
            now, self._swap_rollover.local_time, self._swap_rollover.timezone, triple_weekday_py)
        return multiplier >= 3

    def get_time_to_market_close(self) -> Optional[timedelta]:
        """
        Time from the current clock to the next weekend market close.

        Returns:
            timedelta to the next Friday close, or None for 24/7 markets (crypto)
        """
        if not self._weekend_closure:
            return None
        now = self._clock_fn()
        return MarketCalendar.next_market_close(now) - now

    def is_weekend_ahead(self, within: timedelta) -> bool:
        """
        Whether a weekend market close falls within `within` from the current clock.

        Args:
            within: Look-ahead window

        Returns:
            True if the next weekend close is within the window; False for 24/7 markets
        """
        time_to_close = self.get_time_to_market_close()
        return time_to_close is not None and time_to_close <= within
