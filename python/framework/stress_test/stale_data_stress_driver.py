"""
FiniexTestingIDE - Stale-Data Stress Driver
Drives planned market-data stale windows on the sim time axis (#436).
"""

from datetime import datetime
from typing import List, Optional

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.stress_test_types import StaleDataEvent


def warn_events_outside_range(
    events: List[StaleDataEvent],
    data_start: datetime,
    data_end: datetime,
    logger: ScenarioLogger,
) -> None:
    """
    Overlap guard (#436): warn when a stale window has no (partial) overlap
    with the scenario's actual data range — the event can never fire.

    Args:
        events: All configured stale events (both targets)
        data_start: First tick timestamp of the scenario
        data_end: Last tick timestamp of the scenario
        logger: Scenario logger (warning → §35 pot)
    """
    for event in events:
        if event.stale_end_date <= data_start or event.stale_start_date >= data_end:
            logger.warning(
                f"⚠️ [STRESS] Stale window '{event.label}' "
                f"({event.stale_start_date.isoformat()} → "
                f"{event.stale_end_date.isoformat()}) has no overlap with the "
                f"scenario data range — data deviation, event will never fire")


class StaleDataStressDriver:
    """
    Status-plane injection of planned TICK-source stale windows (sim only).

    The third dispatch driver of the #436 contract surface (live heartbeat ·
    THIS · later #375 TimeEvent): entering a window sets the executor's
    MarketDataStatus stale, logs the pot warning, and edge-dispatches
    on_market_data_stale once; leaving restores fresh + logs the episode span.
    Ticks keep flowing by design — a dead FEED does not freeze the MARKET
    (cutting ticks would also freeze simulated broker-side SL/TP fills).
    While a window is active the OrderGuard rejects new entries
    (STALE_MARKET_DATA), deterministically.
    """

    def __init__(
        self,
        events: List[StaleDataEvent],
        executor: AbstractTradeExecutor,
        decision_logic: AbstractDecisionLogic,
        logger: ScenarioLogger,
    ):
        """
        Args:
            events: The scenario tick source's stale events, sorted by start
            executor: The scenario's trade simulator (status home)
            decision_logic: The decision to notify (edge-triggered)
            logger: Scenario logger (episode warnings → §35 pot)
        """
        self._events = events
        self._executor = executor
        self._decision_logic = decision_logic
        self._logger = logger
        self._idx = 0
        self._active: Optional[StaleDataEvent] = None

    def on_tick(self, tick_time: datetime) -> None:
        """
        Advance the window state machine to the current sim time.

        O(1) amortized: one pointer over the sorted events; the common
        no-event pass is two comparisons.

        Args:
            tick_time: Current tick timestamp (canonical sim clock)
        """
        # Leave an active window (recovery edge)
        if self._active is not None and tick_time >= self._active.stale_end_date:
            self._end_episode(self._active)
            self._active = None
            self._idx += 1

        # Skip windows the tick stream jumped over entirely (data gap swallowed
        # the whole window — nothing was observable, note it and move on)
        while (
            self._active is None
            and self._idx < len(self._events)
            and self._events[self._idx].stale_end_date <= tick_time
        ):
            skipped = self._events[self._idx]
            self._logger.info(
                f"⏭️ [STRESS] Stale window '{skipped.label}' skipped — "
                f"no tick inside the window (data gap)")
            self._idx += 1

        if self._idx >= len(self._events):
            return

        event = self._events[self._idx]
        if tick_time < event.stale_start_date:
            return

        # Inside the window: entry edge fires once, then keep status current
        entered = self._active is None
        if entered:
            self._active = event
            self._logger.warning(
                f"⚠️ [STRESS] Market data stale since "
                f"{event.stale_start_date.strftime('%H:%M:%S')}: "
                f"'{event.label}' (until "
                f"{event.stale_end_date.strftime('%H:%M:%S')}) — "
                f"entries guard-blocked"
            )
        status = MarketDataStatus(
            is_stale=True,
            stale_since=event.stale_start_date,
            seconds_since_last_tick=(
                tick_time - event.stale_start_date).total_seconds(),
        )
        self._executor.set_market_data_status(status)
        if entered:
            self._decision_logic.on_market_data_stale(status)

    def finish(self) -> None:
        """Close a window still active at scenario end (episode span to pot)."""
        if self._active is not None:
            self._end_episode(self._active)
            self._active = None

    def _end_episode(self, event: StaleDataEvent) -> None:
        """
        Recovery edge: fresh status + from–to episode span into the pot.

        Args:
            event: The window being left
        """
        duration_s = (
            event.stale_end_date - event.stale_start_date).total_seconds()
        self._logger.warning(
            f"✅ [STRESS] Market data recovered: stale "
            f"{event.stale_start_date.strftime('%H:%M:%S')} → "
            f"{event.stale_end_date.strftime('%H:%M:%S')} "
            f"({int(duration_s // 60)}m {int(duration_s % 60)}s) — "
            f"'{event.label}'"
        )
        self._executor.set_market_data_status(MarketDataStatus())
