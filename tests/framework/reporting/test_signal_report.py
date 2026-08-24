"""
Signal Report Builder + Aggregate + Render Tests (#433).

`build_signal_report` joins the two planes a signal source has: the ARCHIVE facts from the
real `SignalCoverageReport` (#447) and the RUNTIME counters the SIGNAL workers captured,
carried on the real `RunUnit`s. Tested with REAL types throughout — a real analyzed
`SignalCoverageReport` over a written parquet, real `SignalScenarioInfo` / `RunUnit` /
`SignalResolutionStats` — so a field-name or semantics drift fails loudly instead of being
absorbed by a stand-in. The render test feeds the real model into `SignalSummary`.
"""

import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from python.framework.discoveries.signal_coverage.signal_coverage_report import (
    SignalCoverageReport)
from python.framework.reporting.builders.report_aggregators import aggregate_signal_fresh_ratio
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.builders.signal_report_builder import build_signal_report
from python.framework.reporting.console.signal_summary import SignalSummary
from python.framework.types.api.report_types import SignalReport
from python.framework.types.scenario_types.scenario_set_types import (
    SignalScenarioInfo, SignalScenarioUsage)
from python.framework.signal_data.signal_observed_accumulator import (
    SignalObservedAccumulator)
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SentimentResult, SignalObservedSeries, SignalParquetColumn,
    SignalResolutionStats, SignalSnapshot)
from python.framework.utils.console_renderer import ConsoleRenderer

SOURCE = 'crypto_sentiment_mock'
SYMBOL = 'BTCUSD'
WINDOW_START = datetime(2026, 4, 27, 6, 0, tzinfo=timezone.utc)
WINDOW_END = datetime(2026, 4, 27, 7, 0, tzinfo=timezone.utc)


def _write_signal_parquet(path, snapshots: int = 7, stamped_from: int = 0) -> None:
    """
    A minimal but REAL signal parquet: one envelope sentinel row per snapshot.

    Args:
        path: Target parquet path
        snapshots: Number of envelopes to write
        stamped_from: Index from which trigger_reason is stamped — earlier envelopes
            carry '' (a producer that gained the field mid-archive)
    """
    start = datetime(2026, 4, 27, 5, 30, tzinfo=timezone.utc)
    rows = []
    for i in range(snapshots):
        moment = start + timedelta(minutes=10 * i)
        if i < stamped_from:
            reason = ''
        else:
            reason = 'scheduled' if i else 'boot'
        rows.append({
            SignalParquetColumn.COLLECTED_MSC.value: int(moment.timestamp() * 1000),
            SignalParquetColumn.SYMBOL.value: SIGNAL_ENVELOPE_SYMBOL,
            SignalParquetColumn.DATA_ORIGIN.value: 'synthetic',
            SignalParquetColumn.CONFIG_FINGERPRINT.value: 'mock-1e9e9fc4',
            SignalParquetColumn.TRIGGER_REASON.value: reason,
        })
    pd.DataFrame(rows).to_parquet(path, index=False)


@pytest.fixture(scope='module')
def coverage(tmp_path_factory) -> SignalCoverageReport:
    """A real, analyzed coverage report over a written parquet."""
    path = tmp_path_factory.mktemp('signal') / 'signals.parquet'
    _write_signal_parquet(path)
    report = SignalCoverageReport(data_sentiment_type=SOURCE, symbol=SYMBOL)
    report.analyze([path])
    return report


def _info(coverage: SignalCoverageReport, *scenarios: str) -> SignalScenarioInfo:
    """The source/symbol entry with one usage per scenario (same window)."""
    return SignalScenarioInfo(
        data_sentiment_type=SOURCE,
        symbol=SYMBOL,
        coverage=coverage,
        usages=[
            SignalScenarioUsage(
                scenario_name=name, symbol=SYMBOL,
                window_start=WINDOW_START, window_end=WINDOW_END)
            for name in scenarios
        ],
    )


def _unit(name: str, fresh: int, stale: int, blind: int) -> RunUnit:
    """A run unit carrying one SIGNAL worker's counters."""
    return RunUnit(
        name=name, symbol=SYMBOL,
        signal_statistics=[SignalResolutionStats(
            worker_name='sentiment', signal_kind='llm_sentiment', symbol=SYMBOL,
            fresh_ticks=fresh, stale_ticks=stale, blind_ticks=blind)],
    )


class TestArchivePlane:
    """The source row carries the coverage facts, unchanged."""

    def test_provenance_and_cadence(self, coverage):
        report = build_signal_report({(SOURCE, SYMBOL): _info(coverage, 'a')}, [_unit('a', 10, 0, 0)])
        unit = report.units[0]
        assert unit.source == SOURCE
        assert unit.data_origin == 'synthetic'
        assert unit.config_fingerprint == 'mock-1e9e9fc4'
        assert unit.cadence_seconds == coverage.cadence_seconds
        assert unit.snapshot_count == coverage.snapshot_count

    def test_trigger_reasons_counted_per_envelope(self, coverage):
        report = build_signal_report({(SOURCE, SYMBOL): _info(coverage, 'a')}, [_unit('a', 10, 0, 0)])
        assert report.units[0].trigger_reasons == {'scheduled': 6, 'boot': 1}
        assert report.units[0].trigger_unknown == 0

    def test_unknown_share_travels_with_the_composition(self, tmp_path):
        # A producer that gained trigger_reason mid-archive: the row must carry BOTH
        # numbers, so no renderer can present the composition as the whole archive.
        path = tmp_path / 'partial.parquet'
        _write_signal_parquet(path, snapshots=7, stamped_from=4)
        partial = SignalCoverageReport(data_sentiment_type=SOURCE, symbol=SYMBOL)
        partial.analyze([path])

        report = build_signal_report({(SOURCE, SYMBOL): _info(partial, 'a')}, [_unit('a', 10, 0, 0)])
        assert report.units[0].trigger_reasons == {'scheduled': 3}
        assert report.units[0].trigger_unknown == 4

    def test_empty_map_yields_no_units(self):
        assert build_signal_report({}, [_unit('a', 10, 0, 0)]) == SignalReport(units=[])


class TestRuntimePlane:
    """The usage rows carry what the strategy decided on."""

    def test_counters_land_on_the_matching_scenario(self, coverage):
        report = build_signal_report(
            {(SOURCE, SYMBOL): _info(coverage, 'alpha', 'beta')},
            [_unit('alpha', 900, 100, 0), _unit('beta', 500, 0, 500)])
        rows = {usage.scenario: usage for usage in report.units[0].usages}
        assert (rows['alpha'].fresh_ticks, rows['alpha'].stale_ticks) == (900, 100)
        assert rows['alpha'].fresh_ratio == 0.9
        assert rows['beta'].blind_ticks == 500
        assert rows['beta'].fresh_ratio == 0.5

    def test_scenario_without_a_run_unit_stays_zero(self, coverage):
        """A failed scenario produces no RunUnit — its row must not borrow another's counters."""
        report = build_signal_report(
            {(SOURCE, SYMBOL): _info(coverage, 'ran', 'failed')}, [_unit('ran', 100, 0, 0)])
        rows = {usage.scenario: usage for usage in report.units[0].usages}
        assert (rows['failed'].fresh_ticks, rows['failed'].fresh_ratio) == (0, 0.0)

    def test_several_workers_on_one_source_are_summed(self, coverage):
        unit = _unit('alpha', 100, 0, 0)
        unit.signal_statistics.append(SignalResolutionStats(
            worker_name='sentiment_slow', signal_kind='llm_sentiment', symbol=SYMBOL,
            fresh_ticks=60, stale_ticks=40, blind_ticks=0))
        report = build_signal_report({(SOURCE, SYMBOL): _info(coverage, 'alpha')}, [unit])
        row = report.units[0].usages[0]
        assert (row.fresh_ticks, row.stale_ticks) == (160, 40)


class TestFreshRatioAggregate:
    """The run-wide ratio is the weakest channel, not the average."""

    def test_minimum_over_usages(self, coverage):
        report = build_signal_report(
            {(SOURCE, SYMBOL): _info(coverage, 'good', 'bad')},
            [_unit('good', 1000, 0, 0), _unit('bad', 200, 800, 0)])
        assert aggregate_signal_fresh_ratio(report) == pytest.approx(0.2)

    def test_none_when_nothing_resolved(self, coverage):
        """No SIGNAL tick at all → unset, deliberately NOT 1.0 (that would claim a perfect feed)."""
        report = build_signal_report({(SOURCE, SYMBOL): _info(coverage, 'a')}, [])
        assert aggregate_signal_fresh_ratio(report) is None


class TestRender:
    """The console section renders from the model."""

    def _render(self, report: SignalReport) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            SignalSummary(report).render(ConsoleRenderer())
        return buffer.getvalue()

    def test_decision_basis_and_counters_are_shown(self, coverage):
        report = build_signal_report(
            {(SOURCE, SYMBOL): _info(coverage, 'alpha')}, [_unit('alpha', 900, 100, 0)])
        output = self._render(report)
        assert 'what the strategy actually decided on' in output
        assert '900 fresh · 100 stale · 0 blind' in output
        assert '90.0% fresh' in output

    def test_synthetic_origin_is_marked(self, coverage):
        report = build_signal_report(
            {(SOURCE, SYMBOL): _info(coverage, 'alpha')}, [_unit('alpha', 10, 0, 0)])
        assert 'SYNTHETIC' in self._render(report)

    def test_mock_fingerprint_prefix_survives(self, coverage):
        """The mock prefix must stay visible — it is the mock-versus-real discriminator."""
        report = build_signal_report(
            {(SOURCE, SYMBOL): _info(coverage, 'alpha')}, [_unit('alpha', 10, 0, 0)])
        assert '#mock-1e9e9fc4' in self._render(report)


# ============================================================================
# The FEED plane — a live session has no archive to read its facts out of
# ============================================================================

def _feed(*seqs, trigger: str = 'scheduled', epoch: int = 1,
          cadence: float = 600.0) -> SignalObservedSeries:
    """An accumulated live feed carrying the given sequence positions."""
    accumulator = SignalObservedAccumulator(source=SOURCE, symbol=SYMBOL)
    for seq in seqs:
        accumulator.observe(SignalSnapshot(
            collected_msc=1787508000000 + seq * 600_000,
            schema_version='2.0', seq=seq, stream_epoch=epoch,
            trigger_reason=trigger, data_origin='live',
            config_fingerprint='904c2e16bbfb',
            result=[SentimentResult(symbol=SYMBOL, signal='BUY')]))
    accumulator.set_cadence_seconds(cadence)
    return accumulator.get_observed_series()


class TestFeedPlane:
    """
    What a live session can say about its signal source — and what it must not.

    A live run consumed no archive, so there is no window it either covered or missed. The
    first observation run (2026-08-23) rendered no signal section at all, because the
    builder gated on a scenario map that only the mock path ever fills.
    """

    def test_a_feed_alone_produces_a_row(self):
        """No scenario map, no archive — the counters still have somewhere to go."""
        report = build_signal_report({}, [_unit('session', 977, 0, 0)], _feed(82, 83, 84))
        assert len(report.units) == 1
        assert report.units[0].series_kind == 'feed'
        assert report.units[0].snapshot_count == 3

    def test_neither_plane_produces_nothing(self):
        """A run that bound no signal source at all still renders no section."""
        assert build_signal_report({}, [_unit('session', 10, 0, 0)]).units == []

    def test_the_decision_basis_survives(self):
        report = build_signal_report({}, [_unit('session', 977, 0, 0)], _feed(82, 83, 84))
        usage = report.units[0].usages[0]
        assert (usage.fresh_ticks, usage.stale_ticks, usage.blind_ticks) == (977, 0, 0)
        assert usage.fresh_ratio == 1.0

    def test_composition_and_provenance_come_from_the_envelopes(self):
        report = build_signal_report({}, [_unit('session', 5, 0, 0)], _feed(82, 83, 84))
        unit = report.units[0]
        assert unit.data_origin == 'live'
        assert unit.config_fingerprint == '904c2e16bbfb'
        assert unit.trigger_reasons == {'scheduled': 3}
        assert unit.sequence == 'contiguous 82→84'


class TestFeedClaimsNothingItCannotKnow:
    """
    The regression for the whole design: a default must not become an assertion.

    Both values below would otherwise be produced by a field default and read as a
    measurement — 'no gaps' for an archive that does not exist, '100.0% coverage' for a
    window that was never analysable.
    """

    def _render(self, report: SignalReport) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            SignalSummary(report).render(ConsoleRenderer())
        return buffer.getvalue()

    def test_an_absent_gap_analysis_never_renders_as_no_gaps(self):
        output = self._render(
            build_signal_report({}, [_unit('session', 977, 0, 0)], _feed(82, 83, 84)))
        assert 'no gaps' not in output
        assert 'Archive:' not in output
        assert 'Feed:' in output

    def test_no_coverage_percentage_without_an_archive(self):
        report = build_signal_report({}, [_unit('session', 977, 0, 0)], _feed(82, 83, 84))
        assert report.units[0].usages[0].coverage_ratio is None
        assert 'coverage' not in self._render(report)

    def test_the_cadence_is_labelled_as_the_producer_s_own(self):
        """A session that received three envelopes has no sample to measure a median from."""
        output = self._render(
            build_signal_report({}, [_unit('session', 977, 0, 0)], _feed(82, 83, 84)))
        assert '(producer)' in output
        assert '(measured)' not in output

    def test_the_archive_plane_still_says_measured(self, coverage):
        """The sim path is untouched — same section, same words."""
        output = self._render(
            build_signal_report({(SOURCE, SYMBOL): _info(coverage, 'a')}, [_unit('a', 10, 0, 0)]))
        assert 'Archive:' in output and '(measured)' in output
        assert 'Feed:' not in output


class TestSequencePosition:
    """Where the feed stands in the producer's series."""

    def test_a_hole_is_reported(self):
        series = _feed(82, 85)
        assert series.seq_holes == 2
        assert '2 holes' in series.get_sequence_description()

    def test_an_epoch_restart_is_not_a_hole(self):
        """
        Sequence numbers restart at a producer boot, so the distance across the boundary
        measures nothing — counting it would report a restart as lost data.
        """
        accumulator = SignalObservedAccumulator(source=SOURCE, symbol=SYMBOL)
        for seq, epoch in ((90, 1), (91, 1), (1, 2), (2, 2)):
            accumulator.observe(SignalSnapshot(
                collected_msc=1787508000000 + seq * 600_000, schema_version='2.0',
                seq=seq, stream_epoch=epoch, trigger_reason='scheduled',
                result=[SentimentResult(symbol=SYMBOL, signal='HOLD')]))
        series = accumulator.get_observed_series()
        assert series.seq_holes == 0
        assert series.stream_epochs == {1, 2}

    def test_a_pre_stream_era_is_unverifiable_not_contiguous(self):
        """Absent identity is a distinct state, never a clean verdict."""
        accumulator = SignalObservedAccumulator(source=SOURCE)
        accumulator.observe(SignalSnapshot(
            collected_msc=1787508000000, schema_version='1.0',
            result=[SentimentResult(symbol=SYMBOL, signal='HOLD')]))
        assert 'not verifiable' in accumulator.get_observed_series().get_sequence_description()
