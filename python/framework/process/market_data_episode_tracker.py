"""
FiniexTestingIDE - Market Data Episode Tracker
Observes the tick-stream staleness domain and records disturbance episodes (#451).

The ONE observer both pipelines feed: the live loop and the sim stress driver only SET
`MarketDataStatus` — this unit watches it and derives the episode from the state change.
That is what makes the record honest: a planned stress window and the experienced outage
differ whenever the run ends inside the window or the tick stream never enters it, and no
producer of the status can fake a clean episode here.

It also carries the tick-domain counters (fresh / stale ticks), the twin of the SIGNAL
resolution counters (#433 Part C), so both domains report the same shape.
"""

import time
from datetime import datetime
from typing import List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.disturbance_episode_types import (
    DisturbanceDomain, DisturbanceEpisode, DisturbanceOrigin, MarketDataTickStats)
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.utils.time_utils import format_duration


class MarketDataEpisodeTracker:
    """
    Records observed market-data outage spans + per-tick freshness (#451).

    Fed from the two event sources of the current engine (§9): the tick path and the
    heartbeat. The distinction matters for the episode's start — a flip observed ON a tick
    starts at that tick (the sim stress case: ticks keep flowing, the status is injected),
    while a flip observed on the HEARTBEAT starts at the last tick still seen as fresh (the
    live case: the outage physically began when ticks stopped, the threshold only revealed
    it later).
    """

    def __init__(
        self,
        source: str,
        logger: ScenarioLogger,
        measure_wall_duration: bool = False,
    ):
        """
        Args:
            source: The tick source identity (broker type) the episodes belong to
            logger: Run logger — the recovery span goes into the §35 pot
            measure_wall_duration: True (live) → durations measured on the wall axis
                (§9 duration rule); False (sim) → on the canonical tick axis
        """
        self._source = source
        self._logger = logger
        self._measure_wall_duration = measure_wall_duration

        self._episodes: List[DisturbanceEpisode] = []
        self._open_from: Optional[datetime] = None
        self._open_label: str = ''
        self._open_wall_anchor: float = 0.0

        self._last_tick_time: Optional[datetime] = None
        self._last_tick_wall: float = 0.0

        self._fresh_ticks: int = 0
        self._stale_ticks: int = 0

    def on_tick(
        self,
        now: datetime,
        status: MarketDataStatus,
        injected_label: str = '',
    ) -> None:
        """
        Observe the status on the tick path: count this tick and track the episode edges.

        Args:
            now: Current tick timestamp (canonical clock)
            status: The executor's market-data status for this pass
            injected_label: Label of an outage the source declares as deliberately
                injected ('' = a real outage)
        """
        if status.is_stale:
            self._stale_ticks += 1
        else:
            self._fresh_ticks += 1

        # Wall time is read only where it is the measuring axis (live) — the simulation
        # runs this per tick on the hot path and measures on the canonical axis anyway.
        wall = time.time() if self._measure_wall_duration else 0.0

        # A flip seen on a tick begins with THIS tick — it is the first one the run
        # experienced as stale (the sim stress case, where ticks keep flowing).
        self._observe(now, status, injected_label,
                      episode_start=now, wall_anchor=wall)

        self._last_tick_time = now
        self._last_tick_wall = wall

    def on_heartbeat(
        self,
        now: datetime,
        status: MarketDataStatus,
        injected_label: str = '',
    ) -> None:
        """
        Observe the status between ticks: episode edges only, no tick is counted.

        Args:
            now: Current heartbeat time (canonical clock)
            status: The executor's market-data status for this pass
            injected_label: Label of an outage the source declares as deliberately
                injected ('' = a real outage)
        """
        # A flip seen on the heartbeat means the stream fell silent AFTER the last tick —
        # that tick is the honest start, not the moment the threshold revealed it.
        start = self._last_tick_time or now
        anchor = self._last_tick_wall or time.time()
        self._observe(now, status, injected_label,
                      episode_start=start, wall_anchor=anchor)

    def get_episodes(self, run_end: Optional[datetime] = None) -> List[DisturbanceEpisode]:
        """
        The recorded episodes; an episode still open is returned as never recovered.

        Args:
            run_end: Run end time (canonical) used to measure an open episode; falls
                back to the last observed tick

        Returns:
            All episodes of this source, in observation order
        """
        if self._open_from is None:
            return list(self._episodes)

        end = run_end or self._last_tick_time or self._open_from
        return self._episodes + [self._build_episode(
            stale_to=None, duration_seconds=self._duration_to(end))]

    def get_tick_stats(self) -> MarketDataTickStats:
        """
        The tick-domain resolution counters (#451 Part 4).

        Returns:
            MarketDataTickStats for this source
        """
        return MarketDataTickStats(
            source=self._source,
            fresh_ticks=self._fresh_ticks,
            stale_ticks=self._stale_ticks,
        )

    def _observe(
        self,
        now: datetime,
        status: MarketDataStatus,
        injected_label: str,
        episode_start: datetime,
        wall_anchor: float,
    ) -> None:
        """
        Apply one status observation to the episode state machine.

        Args:
            now: Observation time (canonical clock)
            status: Observed market-data status
            injected_label: Declared injection label ('' = real outage)
            episode_start: The start to stamp when this observation opens an episode
            wall_anchor: The wall-clock anchor matching that start
        """
        # Early exit: fresh and nothing open — the common pass.
        if not status.is_stale and self._open_from is None:
            return

        if status.is_stale:
            if self._open_from is None:
                self._open_from = episode_start
                self._open_label = injected_label
                self._open_wall_anchor = wall_anchor
            elif injected_label and not self._open_label:
                # The injection was declared after the episode opened (the live drill
                # label only becomes readable once the source is actually silent).
                self._open_label = injected_label
            return

        self._close_episode(now)

    def _close_episode(self, recovered_at: datetime) -> None:
        """
        Recovery edge: record the observed span and report it to the §35 pot.

        Args:
            recovered_at: When the source was observed to deliver again
        """
        duration_s = self._duration_to(recovered_at)
        episode = self._build_episode(
            stale_to=recovered_at, duration_seconds=duration_s)
        self._episodes.append(episode)

        label = f" — '{self._open_label}'" if self._open_label else ''
        marker = '[STRESS] ' if self._open_label else ''
        self._logger.warning(
            f"✅ {marker}Market data recovered: stale "
            f"{episode.stale_from.strftime('%H:%M:%S')} → "
            f"{recovered_at.strftime('%H:%M:%S')} "
            f"({format_duration(duration_s)}){label}"
        )

        self._open_from = None
        self._open_label = ''
        self._open_wall_anchor = 0.0

    def _build_episode(
        self, stale_to: Optional[datetime], duration_seconds: float) -> DisturbanceEpisode:
        """
        Build the episode record from the open state.

        Args:
            stale_to: Recovery time, or None when the episode never recovered
            duration_seconds: Measured duration

        Returns:
            The DisturbanceEpisode (unit identity is stamped by the transport)
        """
        return DisturbanceEpisode(
            source=self._source,
            domain=DisturbanceDomain.TICK,
            stale_from=self._open_from,
            stale_to=stale_to,
            duration_seconds=duration_seconds,
            origin=(DisturbanceOrigin.STRESS_INJECTED if self._open_label
                    else DisturbanceOrigin.LIVE_REAL),
            label=self._open_label,
        )

    def _duration_to(self, end: datetime) -> float:
        """
        Measured duration of the open episode up to `end`.

        Args:
            end: End of the measured span (canonical clock)

        Returns:
            Duration in seconds — wall axis live, canonical axis in simulation
        """
        if self._measure_wall_duration:
            return max(0.0, time.time() - self._open_wall_anchor)
        return max(0.0, (end - self._open_from).total_seconds())
