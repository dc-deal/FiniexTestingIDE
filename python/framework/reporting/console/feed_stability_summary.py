"""
FiniexTestingIDE - Feed Stability Summary
Displays the observed disturbance episodes per data source in run reports (#451)
"""

from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import (
    FeedStabilityEpisodeRow, FeedStabilityReport, FeedStabilitySourceRow, RunSummary)
from python.framework.types.disturbance_episode_types import DisturbanceDomain, DisturbanceOrigin
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.utils.time_utils import format_duration


def format_disturbance_line(summary: RunSummary) -> str:
    """
    The one-line disturbance notice for a closing block (#451 Part 3).

    Both pipelines render the same sentence off the RunSummary totals, so a disturbed run
    cannot be mistaken for a clean one at a glance. Calculation and rendering only — whether
    a stale share warrants a warning stays a PostRunValidator advisory.

    Args:
        summary: The run's cross-section KPI summary

    Returns:
        The notice line, '' when the run saw no disturbance at all
    """
    if summary.disturbance_episode_count == 0:
        return ''

    sources = (f"{summary.disturbance_source_count} source"
               f"{'s' if summary.disturbance_source_count != 1 else ''}")
    injected = (f" ({summary.disturbance_stress_injected} stress-injected)"
                if summary.disturbance_stress_injected else '')
    return (f"⚠️  Feed disturbance: {summary.disturbance_episode_count} episodes across "
            f"{sources} — {format_duration(summary.disturbance_stale_seconds)} stale{injected}")


class FeedStabilitySummary(AbstractBatchSummarySection):
    """Renders the feed-stability section from the unified model (#451)."""

    _section_title = '📉 FEED STABILITY'

    def __init__(self, feed_stability_report: FeedStabilityReport):
        """
        Initialize feed stability summary.

        Args:
            feed_stability_report: Unified report (one unit per disturbed source)
        """
        self._units = feed_stability_report.units

    def render(self, renderer: ConsoleRenderer, threshold: int = 9):
        """
        Render feed stability section.

        Args:
            renderer: Console renderer for formatting
            threshold: Max episodes listed per source before the list collapses
        """
        self._render_section_header(renderer)

        indent = '   '
        print(f"{indent}{'Source':<28}{'Domain':<10}{'Stale time':>12}"
              f"{'Episodes':>11}   Origin")
        for unit in self._units:
            print(f"{indent}{unit.source[:27]:<28}{unit.domain:<10}"
                  f"{format_duration(unit.stale_seconds):>12}{unit.episode_count:>11}   "
                  f"{self._format_origins(unit)}")

        for unit in self._units:
            print("")
            self._render_source_detail(unit, threshold=threshold)

    def _render_source_detail(
        self, unit: FeedStabilitySourceRow, threshold: int) -> None:
        """
        Render one source's counter line + its episode spans.

        The counters state how much of the run was decided on degraded data, the spans
        state when and how often — a ratio alone cannot tell one long outage from forty
        short ones, which is the reading this section exists to enable.

        The list collapses above `threshold` regardless of the run's detail setting: a
        long live session accumulates hundreds of short outages, and an unbounded list
        would bury the per-source summary above it. The complete record always stays in
        `feed_stability.json` (and the run log), so nothing is lost by collapsing.
        """
        indent = '   '
        print(f"{indent}📉 {unit.source} ({unit.domain})")
        print(f"{indent}   {self._counter_line(unit)}")

        if len(unit.episodes) > threshold:
            print(f"{indent}     {len(unit.episodes)} episodes — "
                  f"full list in feed_stability.json")
            return

        for episode in unit.episodes:
            self._render_episode(episode, indent)

    def _render_episode(self, episode: FeedStabilityEpisodeRow, indent: str) -> None:
        """Render one episode span; an open episode reads as a dead tail, not a recovery."""
        end = self._stamp(episode.stale_to) if episode.stale_to else 'run end'
        origin = self._format_origin(episode.origin, episode.label)
        unit_tag = f" [{episode.unit}]" if episode.unit else ''
        print(f"{indent}     stale {self._stamp(episode.stale_from)} → {end}   "
              f"({format_duration(episode.duration_seconds)})   {origin}{unit_tag}")

    def _counter_line(self, unit: FeedStabilitySourceRow) -> str:
        """The domain's per-tick counters — signal carries a third (blind) class."""
        total = unit.fresh_ticks + unit.stale_ticks + unit.blind_ticks
        if total == 0:
            return 'no tick counters recorded'

        fresh_pct = unit.fresh_ticks / total * 100
        if unit.domain == DisturbanceDomain.SIGNAL.value:
            return (f"{unit.fresh_ticks:,} fresh · {unit.stale_ticks:,} stale · "
                    f"{unit.blind_ticks:,} blind     ({fresh_pct:.1f}% fresh)")
        return (f"{unit.fresh_ticks:,} fresh · {unit.stale_ticks:,} stale ticks"
                f"     ({fresh_pct:.1f}% fresh)")

    def _format_origins(self, unit: FeedStabilitySourceRow) -> str:
        """Origin column of the table row (a source can carry both kinds)."""
        return ' + '.join(
            self._format_origin(origin, '') for origin in unit.origins) or '—'

    def _format_origin(self, origin: str, label: str) -> str:
        """Origin marker; an injected outage is always marked as deliberate."""
        if origin != DisturbanceOrigin.STRESS_INJECTED.value:
            return origin
        if not label:
            return '🧪 stress-injected'
        return f"🧪 [STRESS] \"{label}\""

    def _stamp(self, iso: str) -> str:
        """Minute-precision timestamp of an ISO string ('' stays empty)."""
        return iso[:16].replace('T', ' ') if iso else ''
