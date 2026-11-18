"""
FiniexTestingIDE - AutoTrader Market Data Watch
Whether an AutoTrader session's market data is fresh, and what is said when it is not (#436, #451).
"""

import time
from datetime import datetime
from python.framework.types.autotrader_types.autotrader_loop_state_types import AutotraderLoopState
from typing import List, Optional

from python.framework.autotrader.operator_alerts.alert_episode import (
    AlertEpisode,
    all_clear_detail,
)
from python.framework.autotrader.operator_alerts.operator_alerts import OperatorAlerts
from python.framework.autotrader.tick_sources.abstract_tick_source import AbstractTickSource
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.stress_test.stale_data_stress_driver import StaleDataStressDriver
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.operator_alert_types import (
    OperatorAlertKind,
    OperatorAlertLevel,
)
from python.framework.types.disturbance_episode_types import DisturbanceEpisode, MarketDataTickStats
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus


class AutotraderMarketDataWatch:
    """
    The session-level market-data staleness contract of an AutoTrader session (#436).

    On an instant without a tick it judges the age of the last real tick: past the threshold the
    status flips stale, entries are guard-blocked, and the decision logic and the operator hear it
    once per episode (#235). A real tick ends the episode. The episode records come from the
    observer (#451), which sees every kind of instant.

    Args:
        config: AutoTrader configuration
        executor: The session's executor — its clock and the market-data status it carries
        decision_logic: Told once per episode that the market data went stale
        tick_source: The tick source, for its reconnects and an injected outage's label
        stale_stress_driver: Planned stale windows of a mock profile (#444); None live
        operator_alerts: What the operator hears out of band (#235)
        all_clear_after_s: How long the data must stay fresh before the all-clear is sent
        logger: The session logger
        state: What the event loop's units share while it runs
    """

    def __init__(
        self,
        config: AutoTraderConfig,
        executor: AbstractTradeExecutor,
        decision_logic: AbstractDecisionLogic,
        tick_source: Optional[AbstractTickSource],
        stale_stress_driver: Optional[StaleDataStressDriver],
        operator_alerts: OperatorAlerts,
        all_clear_after_s: float,
        logger: ScenarioLogger,
        state: AutotraderLoopState,
    ):
        self._executor = executor
        self._decision_logic = decision_logic
        self._tick_source = tick_source
        # #444: planned tick-plane stale windows, present only on a mock profile that
        # declares them. None on every live-adapter session — the block lives in
        # scenario_settings, which a live-adapter profile does not carry at all.
        self._stale_stress_driver = stale_stress_driver
        self._operator_alerts = operator_alerts
        # One episode per symptom: the opening alert, then the all-clear only once the symptom
        # stayed away this long — a feed flapping around its threshold is one episode, not a
        # message per crossing
        self._market_data_alert = AlertEpisode(all_clear_after_s)
        self._logger = logger
        self._state = state

        # #436: market-data staleness contract — no real tick for this many wall
        # seconds → session-level stale (pot warning + on_market_data_stale +
        # OrderGuard entry block). 0 disables. Edge state below; the episode
        # record itself belongs to the observer (#451).
        self._market_data_stale_after_s = config.execution.market_data_stale_after_s
        self._market_stale = False
        self._market_stale_since: Optional[datetime] = None
        self._last_reconnect_count: int = 0

        # #451: the observer that turns the status flips above into episode records.
        # It sees both event sources (tick + heartbeat) and measures on the wall axis
        # (§9 duration rule — the canonical clock is bimodal in a mock replay session).
        #
        # #549: the wall axis is right for LIVE, where the physical silence is the truth, and
        # wrong for a mock REPLAY, where the drill's real extent is its DATA span. The rendered
        # line puts the two side by side: measured 2026-09-24, a ten-minute injected window over
        # 281 stale ticks printed `06:10 → 06:20 (0s)`, and the freeze drill printed a five-month
        # span with a one-second duration. Not a one-line fix — in the same mock session the
        # freeze wants the wall axis and the planned window wants the data axis, so it is a
        # question per EPISODE rather than per session.
        self._market_data_tracker = MarketDataEpisodeTracker(
            source=config.broker_type,
            logger=logger,
            measure_wall_duration=True,
        )

    def on_tick(self) -> None:
        """
        What a tick the broker path just applied means for the market data: it ends a stale
        episode, or the wait for the first tick; it advances a planned stale window; and the
        observer counts it under the status both left behind.
        """
        # #436: a real tick ends a stale episode (recovery, edge reset).
        if self._market_stale:
            self._end_market_stale_episode()
        elif self._state.last_real_tick_wall_time <= 0 and self._market_data_alert.is_open():
            # #235: the first tick ends the wait for it the operator was told about
            self._market_data_alert.clears(self._executor.get_current_time())
        if self._market_data_alert.is_open():
            self._send_market_data_all_clear_if_due()

        # #444: advance the planned stale-window state machine AFTER that recovery and
        # BEFORE the tracker observes — the wall-clock episode resolves itself first,
        # then an injected window re-states its claim, and the tracker sees the status
        # both of them left behind. Ticks keep flowing inside a window by design.
        if self._stale_stress_driver is not None:
            self._stale_stress_driver.on_tick(self._executor.get_current_time())

        # #451: observe the resulting status — counts this tick and closes the
        # episode record from what the session actually experienced.
        self._market_data_tracker.on_tick(
            self._executor.get_current_time(),
            self._executor.get_market_data_status(),
            self._injected_outage_label(),
        )

    def evaluate_staleness(self) -> None:
        """
        Evaluate the session-level market-data staleness contract (#436).

        Runs on the idle heartbeat. Wall-clock is valid here as a DURATION
        measurement (elapsed since the last real tick arrived); episode
        RECORDS (stale_since, log lines) are stamped from the canonical
        clock. Edge-triggered: pot warning + on_market_data_stale fire once
        per fresh→stale episode; recovery happens on the tick path. While
        stale, the readable status stays current (escalation input for
        heartbeat-driven logics).
        """
        self._note_reconnects()

        if self._market_data_stale_after_s <= 0:
            return
        # #444: an injected window owns the status while it runs. Both sources write the
        # SAME field, so without this the wall clock could overwrite a deterministic window
        # — and it would say nothing new: the status is already stale and the edge hook has
        # already fired. When the window ends with the feed still quiet, the next heartbeat
        # evaluates normally and the real outage is reported then.
        if (self._stale_stress_driver is not None
                and self._stale_stress_driver.get_active_label()):
            return
        if self._state.last_real_tick_wall_time <= 0:
            # No tick yet — a startup wait for the status, not an outage. The operator still
            # hears when it outlasts the threshold (#235)
            self._alert_on_missing_first_tick()
            return
        # An age from two wall readings decides the flip — a step of the clock can call a healthy
        # feed stale or keep a dead one fresh (#549)
        seconds_since = time.time() - self._state.last_real_tick_wall_time
        if seconds_since <= self._market_data_stale_after_s:
            return

        flipped = not self._market_stale
        if flipped:
            self._market_stale = True
            self._market_stale_since = self._executor.get_current_time()
        status = MarketDataStatus(
            is_stale=True,
            stale_since=self._market_stale_since,
            seconds_since_last_tick=seconds_since,
            reconnect_count=self._last_reconnect_count,
        )
        self._executor.set_market_data_status(status)
        # #451: the episode opens here — the observer stamps it back to the last tick
        # the session still saw as fresh, not to the pass that revealed the silence.
        self._market_data_tracker.on_heartbeat(
            self._executor.get_current_time(), status, self._injected_outage_label())
        if flipped:
            # One episode, two instants: the last tick is when the feed fell silent — the start
            # the episode record and its recovery line count from — and the flip is when the
            # threshold revealed the silence and entries were blocked. Both are named.
            last_tick = self._state.last_tick_time or self._market_stale_since
            self._logger.warning(
                f"⚠️ Market data stale since "
                f"{self._market_stale_since.strftime('%H:%M:%S')}: no tick since "
                f"{last_tick.strftime('%H:%M:%S')} ({seconds_since:.0f}s, threshold "
                f"{self._market_data_stale_after_s:.0f}s) — entries guard-blocked"
            )
            if self._market_data_alert.opens(self._executor.get_current_time()):
                self._operator_alerts.send(
                    OperatorAlertKind.MARKET_DATA_STALE, OperatorAlertLevel.WARNING,
                    'Market data stale — new entries blocked',
                    f"No tick since {last_tick.strftime('%H:%M:%S')} UTC "
                    f'({seconds_since:.0f}s, threshold {self._market_data_stale_after_s:.0f}s) '
                    f"— stale, new entries blocked since "
                    f"{self._market_stale_since.strftime('%H:%M:%S')} UTC")
            self._decision_logic.on_market_data_stale(status)

    def _alert_on_missing_first_tick(self) -> None:
        """
        #235: tell the operator, once, when no first tick has come within the stale threshold.

        A feed that never delivers its first tick — a socket down at a restart while the REST
        boot went through — would otherwise leave "Session started" as the operator's last word
        while the ping stays green, because the idle passes keep the loop alive. Only the alert:
        the status stays fresh until a first tick exists to measure from (#436). The first tick
        ends the episode, and its all-clear follows the same quiet period as any other.
        """
        if self._market_data_alert.is_open() or self._state.loop_started_monotonic <= 0:
            return
        waited = time.monotonic() - self._state.loop_started_monotonic
        if waited <= self._market_data_stale_after_s:
            return
        self._logger.warning(
            f'⚠️ No market data yet: no tick for {waited:.0f}s since the event loop started '
            f'(threshold {self._market_data_stale_after_s:.0f}s) — the feed may not be connected')
        if self._market_data_alert.opens(self._executor.get_current_time()):
            self._operator_alerts.send(
                OperatorAlertKind.MARKET_DATA_STALE, OperatorAlertLevel.WARNING,
                'No market data since the session started',
                f'No tick for {waited:.0f}s since the event loop started (threshold '
                f'{self._market_data_stale_after_s:.0f}s). The feed may not be connected.')

    def _note_reconnects(self) -> None:
        """
        Surface the outages the tick stream came back from to the session pot, and keep the count
        the market-data status carries current (typed connection events are #375/#331).

        The source counts an outage when the first trade after it arrives (#609) — the very tick
        that ends a stale episode — so the recovery reads the count here too, not only the idle
        heartbeat; otherwise the status after a recovery would miss the outage that just ended.
        """
        if self._tick_source is None:
            return
        reconnects = self._tick_source.get_reconnect_count()
        if reconnects > self._last_reconnect_count:
            self._logger.warning(
                f'🔌 Tick stream reconnected '
                f'(+{reconnects - self._last_reconnect_count}, total {reconnects})')
            self._last_reconnect_count = reconnects

    def _end_market_stale_episode(self) -> None:
        """
        Close a stale episode on tick resumption (#436): fresh status + edge reset.

        The episode RECORD and its pot line come from the MarketDataEpisodeTracker
        (#451), which derives both from the observed status change — so a real and an
        injected outage travel the exact same path into the report.
        """
        self._note_reconnects()
        self._market_stale = False
        self._market_stale_since = None
        self._executor.set_market_data_status(MarketDataStatus(
            reconnect_count=self._last_reconnect_count))
        # #235: the all-clear waits until the data has stayed fresh for the quiet period
        self._market_data_alert.clears(self._executor.get_current_time())

    def _send_market_data_all_clear_if_due(self) -> None:
        """#235: the market data's all-clear, once it has stayed fresh for the quiet period."""
        if self._market_data_alert.all_clear_due(self._executor.get_current_time()):
            self._operator_alerts.send(
                OperatorAlertKind.MARKET_DATA_RECOVERED, OperatorAlertLevel.INFO,
                'Market data fresh again',
                all_clear_detail(self._market_data_alert))

    def _injected_outage_label(self) -> str:
        """
        Outage label of a deliberately injected silence — planned window or tick source.

        Returns:
            The label of whichever injection is running, '' when the silence is real
        """
        # The planned window first: it is the deterministic one, and the two cannot both
        # be running for the same reason. Without it an injected outage would be recorded
        # as a REAL one — the very confusion the label exists to prevent (#451).
        if self._stale_stress_driver is not None:
            planned = self._stale_stress_driver.get_active_label()
            if planned:
                return planned
        if self._tick_source is None:
            return ''
        return self._tick_source.get_injected_outage_label()

    def get_disturbance_episodes(self) -> List[DisturbanceEpisode]:
        """
        The session's observed market-data outage episodes (#451).

        Returns:
            List of DisturbanceEpisode (an episode still open is never-recovered)
        """
        return self._market_data_tracker.get_episodes(self._executor.get_current_time())

    def get_tick_stats(self) -> MarketDataTickStats:
        """
        The session's market-data tick counters (#451 Part 4).

        Returns:
            MarketDataTickStats for the session's tick source
        """
        return self._market_data_tracker.get_tick_stats()
