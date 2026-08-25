"""
FiniexTestingIDE - Signal Summary
Displays the signal-source configuration + resolution quality in run reports (#433)
"""

from python.framework.reporting.console.abstract_batch_summary_section import (
    AbstractBatchSummarySection,
)
from python.framework.types.api.report_types import SignalReport, SignalSourceRow, SignalUsageRow
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.utils.time_utils import format_duration


class SignalSummary(AbstractBatchSummarySection):
    """Renders the signal-configuration section from the unified model (#433)."""

    _section_title = '📡 SIGNAL CONFIGURATION'

    def __init__(self, signal_report: SignalReport):
        """
        Initialize signal summary.

        Args:
            signal_report: Unified signal-configuration report (one unit per source)
        """
        self._units = signal_report.units

    def render(self, renderer: ConsoleRenderer, compact: bool = False, threshold: int = 9):
        """
        Render signal summary section.

        Args:
            renderer: Console renderer for formatting
            compact: If True, collapse usage lists above threshold to a count
            threshold: Max usages to list before collapsing
        """
        self._render_section_header(renderer)

        for i, unit in enumerate(self._units):
            if i > 0:
                print('')
            self._render_source_block(unit, renderer)
            self._render_decision_basis(unit, compact=compact, threshold=threshold)

    def _render_source_block(self, unit: SignalSourceRow, renderer: ConsoleRenderer) -> None:
        """Render one source's archive plane: provenance, cadence, coverage."""
        indent = '   '
        origin = self._format_origin(unit.data_origin)
        fingerprint = f'#{unit.config_fingerprint}' if unit.config_fingerprint else 'unknown'
        # A prompt bump is a SERIES BREAK: different prompts score the same news differently.
        # 'mixed' therefore means this run spans two series, which a reader must see before
        # comparing its numbers against another run's.
        prompt = f' · Prompt: v{unit.prompt_version}' if unit.prompt_version else ''
        print(f'{indent}📡 Source: {unit.source}')
        print(f'{indent}   Origin:   {origin} · Config: {fingerprint}{prompt}')

        # A feed's cadence is the producer's own reported interval, not a median: a
        # session that received three envelopes has no sample to measure.
        measure = 'producer' if unit.series_kind == 'feed' else 'measured'
        cadence = format_duration(unit.cadence_seconds) if unit.cadence_seconds else 'n/a'
        triggers = ' · '.join(
            f'{count:,} {reason}'
            for reason, count in sorted(unit.trigger_reasons.items(), key=lambda kv: -kv[1])
        )
        # A partially stamped archive must say so — otherwise the composition reads
        # as if it covered every snapshot.
        if triggers and unit.trigger_unknown:
            triggers += f' · {unit.trigger_unknown:,} unknown'
        print(f"{indent}   Cadence:  {cadence} ({measure}) · "
              f"Triggers: {triggers or 'unknown'}")

        self._render_extent(unit, indent)

    def _render_extent(self, unit: SignalSourceRow, indent: str) -> None:
        """
        Render the source's extent — an analysed archive, or a feed that was received.

        The two are NOT interchangeable. An archive can be measured for continuity against
        a market calendar; a feed has no window it either covered or missed, so it gets no
        gap verdict at all. Printing an empty gap map as "no gaps" would assert continuity
        for a series that was never analysable — the live outage plane is the
        disturbance-episode section instead.

        Args:
            unit: The source row
            indent: Leading whitespace
        """
        span = f'{self._date(unit.archive_start)} → {self._date(unit.archive_end)}'

        if unit.series_kind == 'feed':
            sequence = f' · {unit.sequence}' if unit.sequence else ''
            print(f'{indent}   Feed:     {span} · '
                  f'{unit.snapshot_count:,} envelopes received{sequence}')
            return

        gaps = ', '.join(
            f'{count} {category}' for category, count in sorted(unit.gap_counts.items())
        ) or 'no gaps'
        print(f'{indent}   Archive:  {span} · {unit.snapshot_count:,} snapshots · {gaps}')

    def _render_decision_basis(
        self, unit: SignalSourceRow, compact: bool, threshold: int) -> None:
        """
        Render one source's runtime plane — what the strategy actually decided on.

        Deliberately labelled as the decision basis, not as "resolution": the archive line
        above says what the source could offer, this says what the run consumed. The two can
        differ legitimately (an archive gap shorter than max_staleness produces no stale tick).
        """
        indent = '   '
        print('')
        print(f'{indent}   Decision basis — what the strategy actually decided on:')

        if not unit.usages:
            print(f'{indent}     (no scenario consumed this source)')
            return

        if compact and len(unit.usages) > threshold:
            print(f'{indent}     {len(unit.usages)} scenarios — see log for full list')
            return

        for usage in unit.usages:
            self._render_usage(usage, indent)

    def _render_usage(self, usage: SignalUsageRow, indent: str) -> None:
        """Render one scenario's window + its fresh/stale/blind counters."""
        window_end = self._date(usage.window_end) or 'open'
        # No coverage figure without an archive to have covered: a default would read as
        # 100% and assert exactly what could not be measured.
        extent = (f'coverage {usage.coverage_ratio * 100:.1f}%'
                  if usage.coverage_ratio is not None else 'session span')
        print(f'{indent}     {usage.scenario} ({usage.symbol})   '
              f'{self._date(usage.window_start)} → {window_end} · {extent}')
        print(f'{indent}       {usage.fresh_ticks:,} fresh · {usage.stale_ticks:,} stale · '
              f'{usage.blind_ticks:,} blind     ({usage.fresh_ratio * 100:.1f}% fresh)')

    def _format_origin(self, origin: str) -> str:
        """Origin label — synthetic data is marked, absence means unknown."""
        if origin == 'synthetic':
            return '🧪 SYNTHETIC — generated data, not a market record'
        return origin or 'unknown'

    def _date(self, iso: str) -> str:
        """Date part of an ISO timestamp ('' stays empty)."""
        return iso[:10] if iso else ''
