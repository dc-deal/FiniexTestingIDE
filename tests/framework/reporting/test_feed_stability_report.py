"""
Feed Stability Report Builder + Render Tests (#451).

`build_feed_stability_report` groups the observed disturbance episodes of BOTH staleness
domains — the tick stream (#436) and every SIGNAL source (#434) — into one per-source
table, resolves the source identity the capture sites could not know, and labels each
episode's origin. Tested with REAL types throughout (real `RunUnit`, `DisturbanceEpisode`,
`StaleDataEvent`, `SignalResolutionStats`), so a semantics drift fails loudly.

The binding rule under test: an episode's timestamps always come from the observed state
change; the stress configuration contributes the label alone.
"""

import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from python.framework.reporting.builders.feed_stability_report_builder import (
    build_feed_stability_report,
)
from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.console.feed_stability_summary import (
    FeedStabilitySummary,
    format_disturbance_line,
)
from python.framework.types.api.report_types import (
    ExecutionStatsReport,
    ExecutionStatsTotals,
    PortfolioReport,
    RunSummary,
    TradeHistoryReport,
)
from python.framework.types.disturbance_episode_types import (
    DisturbanceDomain,
    DisturbanceEpisode,
    DisturbanceOrigin,
    MarketDataTickStats,
)
from python.framework.types.signal_data_types import SignalResolutionStats
from python.framework.types.trading_env_types.stress_test_types import StaleDataEvent
from python.framework.utils.console_renderer import ConsoleRenderer

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

TICK_SOURCE = 'kraken_spot'
SIGNAL_SOURCE = 'crypto_sentiment'
SYMBOL = 'BTCUSD'
T0 = datetime(2026, 4, 30, 6, 0, tzinfo=timezone.utc)


def _at(minutes: int) -> datetime:
    """Episode axis: 06:00 UTC plus N minutes."""
    return T0 + timedelta(minutes=minutes)


def _tick_episode(start: int, end, label: str = '') -> DisturbanceEpisode:
    """One tick-domain episode; `end` None = never recovered."""
    stale_to = _at(end) if end is not None else None
    measured = (stale_to or _at(start)) - _at(start)
    return DisturbanceEpisode(
        source=TICK_SOURCE,
        domain=DisturbanceDomain.TICK,
        stale_from=_at(start),
        stale_to=stale_to,
        duration_seconds=measured.total_seconds(),
        origin=(DisturbanceOrigin.STRESS_INJECTED if label
                else DisturbanceOrigin.LIVE_REAL),
        label=label,
    )


def _signal_episode(start: int, end) -> DisturbanceEpisode:
    """One signal-domain episode — captured WITHOUT a source (resolved per unit)."""
    stale_to = _at(end) if end is not None else None
    measured = (stale_to or _at(start)) - _at(start)
    return DisturbanceEpisode(
        source='',
        domain=DisturbanceDomain.SIGNAL,
        stale_from=_at(start),
        stale_to=stale_to,
        duration_seconds=measured.total_seconds(),
        symbol=SYMBOL,
    )


def _unit(episodes, planned=None, signal_stats=None, tick_stats=True) -> RunUnit:
    """A run unit carrying the captured episodes (identity already stamped)."""
    for episode in episodes:
        episode.unit_name = 'BTCUSD_long'
        episode.symbol = episode.symbol or SYMBOL
    return RunUnit(
        name='BTCUSD_long',
        symbol=SYMBOL,
        data_source=TICK_SOURCE,
        sentiment_source=SIGNAL_SOURCE,
        disturbance_episodes=episodes,
        planned_outages=planned or [],
        signal_statistics=signal_stats or [],
        market_data_tick_stats=(
            MarketDataTickStats(source=TICK_SOURCE, fresh_ticks=900, stale_ticks=100)
            if tick_stats else None),
    )


def _planned(label: str, source: str, start: int, end: int) -> StaleDataEvent:
    """One configured stale window."""
    return StaleDataEvent(
        label=label, data_source=source,
        stale_start_date=_at(start), stale_end_date=_at(end))


def _summary_over(report) -> RunSummary:
    """Compose a RunSummary over empty trading sections — only the #451 totals matter."""
    return build_run_summary(_RUN_ID, 
        PortfolioReport(run_id=_RUN_ID, units=[], aggregates=[]),
        TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[], analytics=[]),
        ExecutionStatsReport(run_id=_RUN_ID, units=[], totals=ExecutionStatsTotals()),
        None,
        report,
    )


class TestEmptyRun:
    """A clean run produces no section at all."""

    def test_no_episodes_no_rows(self):
        report = build_feed_stability_report(_RUN_ID, [_unit([])])
        assert report.units == []
        assert report.episode_count == 0

    def test_no_units_at_all(self):
        assert build_feed_stability_report(_RUN_ID, []).units == []


class TestGrouping:
    """One row per source across both domains."""

    def test_both_domains_in_one_table(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_tick_episode(10, 25), _signal_episode(30, None)],
            signal_stats=[SignalResolutionStats(
                worker_name='sentiment', signal_kind='llm_sentiment', symbol=SYMBOL,
                fresh_ticks=400, stale_ticks=600, blind_ticks=0)],
        )])

        assert report.source_count == 2
        by_source = {row.source: row for row in report.units}
        assert by_source[TICK_SOURCE].domain == DisturbanceDomain.TICK.value
        assert by_source[SIGNAL_SOURCE].domain == DisturbanceDomain.SIGNAL.value
        # The signal source identity comes from the unit, not from the worker's kind
        assert by_source[SIGNAL_SOURCE].episodes[0].stale_to == ''

    def test_counters_attach_to_their_domain(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_tick_episode(10, 25), _signal_episode(30, 40)],
            signal_stats=[SignalResolutionStats(
                worker_name='sentiment', signal_kind='llm_sentiment', symbol=SYMBOL,
                fresh_ticks=400, stale_ticks=600, blind_ticks=5)],
        )])

        by_source = {row.source: row for row in report.units}
        assert by_source[TICK_SOURCE].fresh_ticks == 900
        assert by_source[TICK_SOURCE].stale_ticks == 100
        assert by_source[TICK_SOURCE].blind_ticks == 0     # no blind class on ticks
        assert by_source[SIGNAL_SOURCE].fresh_ticks == 400
        assert by_source[SIGNAL_SOURCE].blind_ticks == 5

    def test_episodes_sum_across_units(self):
        report = build_feed_stability_report(_RUN_ID, [
            _unit([_tick_episode(10, 25)]),
            _unit([_tick_episode(40, 50)]),
        ])
        assert report.source_count == 1
        assert report.units[0].episode_count == 2
        assert report.units[0].stale_seconds == 1500.0     # 15 min + 10 min
        assert report.units[0].fresh_ticks == 1800         # both units' counters


class TestOriginJoin:
    """live-real vs stress-injected — the configuration contributes the label only."""

    def test_overlapping_window_marks_the_episode_injected(self):
        # The signal flip lands INSIDE the window, later than its start (the staleness
        # threshold has to elapse first) — containment would miss it, overlap does not.
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_signal_episode(45, 70)],
            planned=[_planned('feed dies', SIGNAL_SOURCE, 30, 90)],
        )])
        episode = report.units[0].episodes[0]
        assert episode.origin == DisturbanceOrigin.STRESS_INJECTED.value
        assert episode.label == 'feed dies'
        assert report.stress_injected_count == 1

    def test_disjoint_window_leaves_the_episode_real(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_signal_episode(120, 140)],
            planned=[_planned('feed dies', SIGNAL_SOURCE, 30, 90)],
        )])
        assert report.units[0].episodes[0].origin == DisturbanceOrigin.LIVE_REAL.value
        assert report.stress_injected_count == 0

    def test_window_of_another_source_does_not_match(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_signal_episode(45, 70)],
            planned=[_planned('other feed', 'forex_macro_sentiment', 30, 90)],
        )])
        assert report.units[0].episodes[0].origin == DisturbanceOrigin.LIVE_REAL.value

    def test_open_episode_matches_a_later_window(self):
        """An episode that never recovered reaches to the run end."""
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_signal_episode(45, None)],
            planned=[_planned('feed dies', SIGNAL_SOURCE, 30, 900)],
        )])
        assert report.units[0].episodes[0].origin == DisturbanceOrigin.STRESS_INJECTED.value

    def test_capture_side_label_survives(self):
        """A capture site that KNOWS it injected is never overruled by the join."""
        report = build_feed_stability_report(_RUN_ID, [_unit([_tick_episode(10, 25, 'freeze drill')])])
        episode = report.units[0].episodes[0]
        assert episode.origin == DisturbanceOrigin.STRESS_INJECTED.value
        assert episode.label == 'freeze drill'


class TestRunSummaryTotals:
    """The executive line reads the totals — it does not re-scan the episodes."""

    def test_totals_reach_the_run_summary(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_tick_episode(10, 25, 'w1'), _signal_episode(30, None)])])
        summary = _summary_over(report)
        assert summary.disturbance_episode_count == 2
        assert summary.disturbance_source_count == 2
        assert summary.disturbance_stress_injected == 1
        assert summary.disturbance_stale_seconds == 900.0   # 15 min + 0 (open at start)

    def test_clean_run_has_no_disturbance_line(self):
        summary = _summary_over(build_feed_stability_report(_RUN_ID, [_unit([])]))
        assert summary.disturbance_episode_count == 0
        assert format_disturbance_line(summary) == ''


class TestRender:
    """The console section renders from the model (PRESENT only)."""

    def _render(self, report) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            FeedStabilitySummary(report).render(ConsoleRenderer())
        return buffer.getvalue()

    def test_section_shows_both_domains_and_the_open_tail(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_tick_episode(10, 25), _signal_episode(30, None)],
            planned=[_planned('feed dies 12h', SIGNAL_SOURCE, 20, 900)],
            signal_stats=[SignalResolutionStats(
                worker_name='sentiment', signal_kind='llm_sentiment', symbol=SYMBOL,
                fresh_ticks=400, stale_ticks=600, blind_ticks=0)],
        )])
        output = self._render(report)

        assert 'FEED STABILITY' in output
        assert TICK_SOURCE in output and SIGNAL_SOURCE in output
        assert 'run end' in output                 # the never-recovered tail
        assert 'feed dies 12h' in output           # the injected label
        assert '600 stale' in output               # the signal counters

    def test_long_episode_list_collapses(self):
        """A live session with many short outages must not bury the source summary."""
        episodes = [_tick_episode(i * 10, i * 10 + 5) for i in range(12)]
        report = build_feed_stability_report(_RUN_ID, [_unit(episodes)])
        output = self._render(report)

        assert '12 episodes — full list in feed_stability.json' in output
        assert 'stale 2026-04-30 06:10' not in output   # no single span listed
        assert '12' in output                            # the count row still reads
        # The model keeps every episode — only the console collapses
        assert len(report.units[0].episodes) == 12

    def test_disturbance_line_wording(self):
        report = build_feed_stability_report(_RUN_ID, [_unit(
            [_tick_episode(10, 25, 'w1'), _tick_episode(40, 50)])])
        summary = _summary_over(report)
        line = format_disturbance_line(summary)
        assert '2 episodes' in line and '1 source' in line
        assert '1 stress-injected' in line
