"""
FiniexTestingIDE - Signal Resolution Counters — Simulation Pipeline (#433 Part C)

Runs six deterministic cases over the gap-free synthetic sentiment archive, every anomaly
carved by the stress module, and asserts what the strategy actually DECIDED ON (tick-weighted
fresh / stale / blind), not what the archive could offer.

The two cases that carry the whole argument:
- restart_short_gap: a carved hole SHORTER than max_staleness_minutes costs zero stale ticks
  (an archive gap is not a stale run), so it must be counter-identical to the clean control.
- tight_threshold: no carve at all — the same data as the clean control, but a threshold
  tighter than the producer cadence turns half the run stale. Proves the counters measure
  data × parameter, which no archive-side report can.
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.reporting.builders.report_aggregators import aggregate_signal_fresh_ratio
from python.framework.reporting.builders.run_unit import run_units_from_batch
from python.framework.reporting.builders.signal_report_builder import build_signal_report
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.scenario.scenario_config_loader import ScenarioConfigLoader

FIXTURE_SET = (
    Path(__file__).resolve().parents[3]
    / 'tests' / 'fixtures' / 'scenario_sets' / 'signal_resolution'
    / 'signal_resolution_cases.json'
)

# Scenario order in the fixture set (index-synced with process_result_list)
CLEAN = 0
RESTART_SHORT_GAP = 1
OUTAGE_2H = 2
STALE_TAIL = 3
BLIND_HEAD = 4
TIGHT_THRESHOLD = 5


@pytest.fixture(scope='module')
def summary():
    """Run the 6-scenario resolution set once, shared across all tests."""
    scenario_config = ScenarioConfigLoader().load_config(str(FIXTURE_SET))
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config)
    return BatchOrchestrator(scenario_set, app_config).run()


def _counters(summary, index: int):
    """The scenario's single SIGNAL worker counters + its processed tick count."""
    result = summary.process_result_list[index]
    assert result.success, f'Scenario {index} failed: {result.error_message}'
    stats = result.tick_loop_results.signal_statistics
    assert len(stats) == 1, 'the fixture binds exactly one SIGNAL worker per scenario'
    return stats[0], result.tick_loop_results.coordination_statistics.ticks_processed


class TestTickExactness:
    """The structural invariant: every tick is counted exactly once, in exactly one class."""

    @pytest.mark.parametrize('index', [
        CLEAN, RESTART_SHORT_GAP, OUTAGE_2H, STALE_TAIL, BLIND_HEAD, TIGHT_THRESHOLD])
    def test_counters_sum_to_tick_count(self, summary, index):
        stats, ticks = _counters(summary, index)
        assert stats.fresh_ticks + stats.stale_ticks + stats.blind_ticks == ticks

    def test_worker_identity_is_carried(self, summary):
        stats, _ = _counters(summary, CLEAN)
        assert stats.worker_name == 'sentiment'
        assert stats.signal_kind == 'llm_sentiment'
        assert stats.symbol == 'BTCUSD'


class TestCleanControl:
    """No carve, threshold above the cadence → nothing but fresh."""

    def test_all_fresh(self, summary):
        stats, ticks = _counters(summary, CLEAN)
        assert stats.fresh_ticks == ticks
        assert stats.stale_ticks == 0
        assert stats.blind_ticks == 0


class TestArchiveGapIsNotAStaleRun:
    """A carved hole shorter than max_staleness_minutes never reaches the decision."""

    def test_short_gap_costs_no_stale_tick(self, summary):
        stats, ticks = _counters(summary, RESTART_SHORT_GAP)
        assert stats.stale_ticks == 0
        assert stats.blind_ticks == 0
        assert stats.fresh_ticks == ticks

    def test_identical_to_the_clean_control(self, summary):
        """Same window, same ticks — the carve is invisible to the decision."""
        carved, _ = _counters(summary, RESTART_SHORT_GAP)
        clean, _ = _counters(summary, CLEAN)
        assert (carved.fresh_ticks, carved.stale_ticks, carved.blind_ticks) == \
               (clean.fresh_ticks, clean.stale_ticks, clean.blind_ticks)


class TestOutage:
    """A carved outage longer than the threshold: stale in the middle, recovery inside."""

    def test_stale_majority_with_recovery(self, summary):
        stats, _ = _counters(summary, OUTAGE_2H)
        assert stats.stale_ticks > 0
        assert stats.fresh_ticks > 0      # recovered before the window closed
        assert stats.blind_ticks == 0     # something always resolved — an outage is not blind

    def test_tail_never_recovers(self, summary):
        """The feed dies and stays dead: stale to the last tick, still never blind."""
        stats, _ = _counters(summary, STALE_TAIL)
        assert stats.stale_ticks > 0
        assert stats.fresh_ticks > 0
        assert stats.blind_ticks == 0


class TestBlindHead:
    """Nothing resolvable before the first surviving snapshot — the only blind case."""

    def test_blind_then_fresh(self, summary):
        stats, _ = _counters(summary, BLIND_HEAD)
        assert stats.blind_ticks > 0
        assert stats.fresh_ticks > 0
        assert stats.stale_ticks == 0


class TestThresholdIsAParameter:
    """The decisive pair: same data as the clean control, different staleness threshold."""

    def test_tight_threshold_turns_the_run_stale_without_touching_the_data(self, summary):
        stats, ticks = _counters(summary, TIGHT_THRESHOLD)
        assert stats.stale_ticks > 0
        assert stats.blind_ticks == 0
        assert stats.fresh_ticks + stats.stale_ticks == ticks

    def test_clean_control_has_none_of_it(self, summary):
        """Identical window and archive — only max_staleness_minutes differs."""
        tight, tight_ticks = _counters(summary, TIGHT_THRESHOLD)
        clean, clean_ticks = _counters(summary, CLEAN)
        assert tight_ticks == clean_ticks
        assert clean.stale_ticks == 0 and tight.stale_ticks > 0


class TestReportProjection:
    """The counters reach the model, keyed to the right scenario."""

    @pytest.fixture(scope='class')
    def report(self, summary):
        return build_signal_report(
            summary.signal_scenario_map, run_units_from_batch(summary))

    def test_one_source_with_every_scenario(self, report):
        assert len(report.units) == 1
        unit = report.units[0]
        assert unit.source == 'crypto_sentiment_mock'
        assert unit.data_origin == 'synthetic'
        assert len(unit.usages) == 6

    def test_usage_rows_carry_the_counters(self, report, summary):
        rows = {usage.scenario: usage for usage in report.units[0].usages}
        for index, name in ((CLEAN, 'clean'), (OUTAGE_2H, 'outage_2h'),
                            (BLIND_HEAD, 'blind_head')):
            stats, _ = _counters(summary, index)
            row = rows[name]
            assert (row.fresh_ticks, row.stale_ticks, row.blind_ticks) == \
                   (stats.fresh_ticks, stats.stale_ticks, stats.blind_ticks)

    def test_clean_row_is_fully_fresh(self, report):
        rows = {usage.scenario: usage for usage in report.units[0].usages}
        assert rows['clean'].fresh_ratio == 1.0

    def test_run_ratio_is_the_weakest_channel(self, report):
        ratios = [usage.fresh_ratio for usage in report.units[0].usages]
        assert aggregate_signal_fresh_ratio(report) == min(ratios)
