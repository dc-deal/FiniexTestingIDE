"""
FiniexTestingIDE - Stale-Data Stress Driver
Drives planned market-data stale windows on a run's own time axis (#436, #444).
"""

from datetime import datetime
from typing import List, Optional, Tuple

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.stress_test_types import (
    StaleDataEvent,
    StressTestConfig,
)


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
    Status-plane injection of planned TICK-source stale windows (both pipelines).

    The third dispatch driver of the #436 contract surface (live heartbeat ·
    THIS · later #375 TimeEvent): entering a window sets the executor's
    MarketDataStatus stale, logs the pot warning, and edge-dispatches
    on_market_data_stale once; leaving restores fresh. The episode record and
    its span come from the observed status change (MarketDataEpisodeTracker,
    #451), never from the planned window.
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
        """Close a window still active at scenario end (status reset only)."""
        if self._active is not None:
            self._end_episode(self._active)
            self._active = None

    def get_active_label(self) -> str:
        """
        Label of the window currently being injected (#451 episode origin).

        Returns:
            The active event's label, '' when no window is active
        """
        return self._active.label if self._active is not None else ''

    def _end_episode(self, event: StaleDataEvent) -> None:
        """
        Recovery edge: restore the fresh status.

        The episode RECORD (span + pot line) belongs to the MarketDataEpisodeTracker,
        which derives it from the observed status change (#451) — this driver plans the
        window, it does not get to claim what the run experienced.

        Args:
            event: The window being left
        """
        self._executor.set_market_data_status(MarketDataStatus())


def build_stale_stress_driver(
    stress_config: Optional[StressTestConfig],
    data_source: str,
    data_range: Optional[Tuple[datetime, datetime]],
    executor: AbstractTradeExecutor,
    decision_logic: AbstractDecisionLogic,
    logger: ScenarioLogger,
) -> Optional[StaleDataStressDriver]:
    """
    Build the tick-source stale-window driver a run's stress config asks for (#444).

    Both pipelines construct it HERE rather than each in its own loop: the selection
    (which events hit this tick source), the overlap guard and the decision not to build
    at all are one rule, and a rule written twice is the one that drifts (§19). The
    SIGNAL plane needs no counterpart — its windows are carved out of the series at
    preparation time, on a path both pipelines already share.

    Args:
        stress_config: The run's parsed stress configuration, or None
        data_source: The TICK data source to select events for (the broker type)
        data_range: First and last tick timestamp of the run, for the overlap guard.
            None means the run has no ticks — there is nothing to inject into, so no
            driver is built
        executor: The run's executor (where the market-data status lives)
        decision_logic: The decision notified on the window edges
        logger: The run logger (warnings → §35 pot)

    Returns:
        The driver, or None when no planned window hits this tick source
    """
    stale_config = stress_config.stale_data_stress if stress_config else None
    if stale_config is None or not stale_config.enabled or data_range is None:
        return None

    # Guarded against ALL events, not only this source's: a signal window that can never
    # fire is just as worth reporting, and this is the one place holding the data range.
    warn_events_outside_range(
        stale_config.events, data_range[0], data_range[1], logger)

    events = stale_config.get_events_for_source(data_source)
    if not events:
        return None
    return StaleDataStressDriver(events, executor, decision_logic, logger)
