"""
FiniexTestingIDE - Sim Pass Hooks (#458)

The backtest runner's own steps inside the loop core's tick pass.
"""

import time
from typing import Dict, Optional

from python.framework.loop.abstract_backtest_pass_hooks import AbstractBacktestPassHooks
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.reporting.booking_period_recorder import BookingPeriodRecorder
from python.framework.stress_test.stale_data_stress_driver import StaleDataStressDriver
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.market_types.market_data_types import TickData


class SimPassHooks(AbstractBacktestPassHooks):
    """
    The equity sample, the stress driver and the market-data tracker of one backtest scenario.

    Built once per scenario by its runner, which owns every collaborator here.

    Args:
        trade_simulator: The scenario's executor, whose market-data status the tracker records
        portfolio: The scenario's portfolio, sampled for the drawdown and the booking period
        booking: The scenario's booking period recorder, whose valuation rides the sample
        stale_stress_driver: The planned stale windows on the tick source (#436), or None
        market_data_tracker: The tick-domain observer (#451)
        profiling_enabled: Whether the scenario times its steps
        profile_times: The scenario's step timings in milliseconds
        profile_counts: How often each timed step ran
    """

    def __init__(
        self,
        trade_simulator: AbstractTradeExecutor,
        portfolio: PortfolioManager,
        booking: BookingPeriodRecorder,
        stale_stress_driver: Optional[StaleDataStressDriver],
        market_data_tracker: MarketDataEpisodeTracker,
        profiling_enabled: bool,
        profile_times: Dict[str, float],
        profile_counts: Dict[str, int],
    ):
        self._trade_simulator = trade_simulator
        self._portfolio = portfolio
        self._booking = booking
        self._stale_stress_driver = stale_stress_driver
        self._market_data_tracker = market_data_tracker
        self._profiling_enabled = profiling_enabled
        self._profile_times = profile_times
        self._profile_counts = profile_counts

    def observe_market(self) -> None:
        """
        Sample the equity (#497) and hand the same figure to the booking period (#537).

        Before the clipping gate, with the broker path and the bar rendering, and for the same
        reason they are there: a drawdown measures what the MARKET did to the account, not whether
        the algo got to look. One evaluation serves both, because on MARGIN it costs 6.7 % of tick
        time (measured 2026-09-14). The tick counts even when the holdings cannot be valued yet.
        """
        if self._profiling_enabled:
            started = time.perf_counter()
            self._booking.observe_tick(self._portfolio.sample_equity())
            self._profile_times['equity_sample'] += (time.perf_counter() - started) * 1000
            self._profile_counts['equity_sample'] += 1
            return
        self._booking.observe_tick(self._portfolio.sample_equity())

    def prepare_algo(self, tick: TickData) -> None:
        """
        Advance the planned stale windows (#436) and record the resulting market-data status (#451).

        Args:
            tick: The tick the algo is about to see
        """
        driver = self._stale_stress_driver
        if driver is not None:
            driver.on_tick(tick.timestamp)
        self._market_data_tracker.on_tick(
            tick.timestamp,
            self._trade_simulator.get_market_data_status(),
            driver.get_active_label() if driver else '',
        )
