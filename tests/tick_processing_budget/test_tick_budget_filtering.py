"""
Tick Processing Budget — Filtering Tests
==========================================
Tests for _apply_tick_budget() virtual clock algorithm.

Covers:
- Basic filtering with known tick sequences
- Determinism (same input = same output)
- Edge cases (empty, single tick, pre-V1.3.0 data)
- Sub-millisecond budget (data granularity limitation)
- ClippingStats correctness
"""

import pytest

from python.framework.types.process_data_types import ClippingStats
from tests.tick_processing_budget.conftest import make_tick, make_scenario_ticks


SYMBOL = 'BTCUSD'


# =============================================================================
# BASIC FILTERING
# =============================================================================

class TestVirtualClockFiltering:
    """Core virtual clock algorithm tests."""

    def test_budget_2ms_known_sequence(self, preparator, sparse_ticks):
        """
        Budget 2ms with ticks at 1000,1001,1002,1003,1005,1008.

        Expected:
        - 1000: keep (clock→1002)
        - 1001: clip (1001 < 1002)
        - 1002: keep (clock→1004)
        - 1003: clip (1003 < 1004)
        - 1005: keep (clock→1007)
        - 1008: keep (clock→1010)
        Result: 4 kept, 2 clipped
        """
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert stats.ticks_total == 6
        assert stats.ticks_kept == 4
        assert stats.ticks_clipped == 2
        assert stats.budget_ms == 2.0

        kept = filtered['ticks'][SYMBOL]
        kept_msc = [t['collected_msc'] for t in kept]
        assert kept_msc == [1000, 1002, 1005, 1008]

    def test_budget_1ms_integer_spacing(self, preparator, regular_ticks):
        """
        Budget 1ms with 1ms-spaced ticks.

        Every tick passes: tick[i].collected_msc = 1000+i,
        virtual_clock after tick[i] = 1000+i+1 = tick[i+1].collected_msc.
        All 10 ticks kept.
        """
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 1.0)

        assert stats.ticks_total == 10
        assert stats.ticks_kept == 10
        assert stats.ticks_clipped == 0

    def test_large_budget_clips_most(self, preparator, regular_ticks):
        """Budget 5ms with 1ms spacing — only every 5th tick survives."""
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 5.0)

        # 1000: keep (clock→1005), 1005: keep (clock→1010) — only 2
        assert stats.ticks_kept == 2
        assert stats.ticks_clipped == 8

        kept = filtered['ticks'][SYMBOL]
        kept_msc = [t['collected_msc'] for t in kept]
        assert kept_msc == [1000, 1005]

    def test_first_tick_always_kept(self, preparator):
        """First tick always passes (virtual_clock starts at 0)."""
        ticks = [make_tick(500)]
        scenario_ticks = make_scenario_ticks(SYMBOL, ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 999.0)

        assert stats.ticks_kept == 1
        assert stats.ticks_clipped == 0

    def test_budget_preserves_ranges(self, preparator, sparse_ticks):
        """Filtering must preserve the original 'ranges' dict."""
        ranges = {SYMBOL: ('2026-01-01', '2026-01-02')}
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        scenario_ticks['ranges'] = ranges

        filtered, _ = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert filtered['ranges'] is ranges

    def test_counts_match_kept_ticks(self, preparator, sparse_ticks):
        """Filtered counts dict must reflect actual kept tick count."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert filtered['counts'][SYMBOL] == stats.ticks_kept
        assert len(filtered['ticks'][SYMBOL]) == stats.ticks_kept


# =============================================================================
# DETERMINISM
# =============================================================================

class TestDeterminism:
    """Budget filtering must be deterministic."""

    def test_same_input_same_output(self, preparator, sparse_ticks):
        """Two runs with identical input produce identical output."""
        scenario_ticks_1 = make_scenario_ticks(SYMBOL, sparse_ticks)
        scenario_ticks_2 = make_scenario_ticks(SYMBOL, sparse_ticks)

        _, stats_1 = preparator._apply_tick_budget(scenario_ticks_1, SYMBOL, 2.0)
        _, stats_2 = preparator._apply_tick_budget(scenario_ticks_2, SYMBOL, 2.0)

        assert stats_1.ticks_kept == stats_2.ticks_kept
        assert stats_1.ticks_clipped == stats_2.ticks_clipped
        assert stats_1.clipping_rate_pct == stats_2.clipping_rate_pct

    def test_different_budgets_different_results(self, preparator, sparse_ticks):
        """Different budgets must produce different clipping counts."""
        scenario_ticks_1 = make_scenario_ticks(SYMBOL, sparse_ticks)
        scenario_ticks_2 = make_scenario_ticks(SYMBOL, sparse_ticks)

        _, stats_small = preparator._apply_tick_budget(scenario_ticks_1, SYMBOL, 1.0)
        _, stats_large = preparator._apply_tick_budget(scenario_ticks_2, SYMBOL, 3.0)

        assert stats_large.ticks_clipped > stats_small.ticks_clipped


# =============================================================================
# EDGE CASES
# =============================================================================

class TestEdgeCases:
    """Edge case coverage for budget filtering."""

    def test_empty_ticks(self, preparator):
        """Empty tick list returns zero-stats with budget recorded."""
        scenario_ticks = make_scenario_ticks(SYMBOL, [])
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert stats.ticks_total == 0
        assert stats.ticks_kept == 0
        assert stats.ticks_clipped == 0
        assert stats.budget_ms == 2.0

    def test_pre_v13_data_skips_filtering(self, preparator, pre_v13_ticks):
        """Pre-V1.3.0 data (collected_msc=0) skips filtering, keeps all ticks."""
        scenario_ticks = make_scenario_ticks(SYMBOL, pre_v13_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert stats.ticks_total == 3
        assert stats.ticks_kept == 3
        assert stats.ticks_clipped == 0
        assert stats.budget_ms == 2.0

        # All original ticks preserved
        assert len(filtered['ticks'][SYMBOL]) == 3

    def test_pre_v13_logs_warning(self, preparator, pre_v13_ticks, mock_logger):
        """Pre-V1.3.0 data must log a warning."""
        scenario_ticks = make_scenario_ticks(SYMBOL, pre_v13_ticks)
        preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args[0][0]
        assert 'pre-V1.3.0' in call_args

    def test_sub_ms_budget_no_clipping(self, preparator, regular_ticks):
        """
        Budget < 1.0ms with integer-ms ticks clips nothing.

        Ticks spaced 1ms apart, budget 0.3ms:
        virtual_clock after tick[0] = 1000 + 0.3 = 1000.3
        tick[1].collected_msc = 1001 >= 1000.3 → kept
        All ticks pass.
        """
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 0.3)

        assert stats.ticks_total == 10
        assert stats.ticks_kept == 10
        assert stats.ticks_clipped == 0

    def test_symbol_not_in_ticks(self, preparator):
        """Unknown symbol returns empty stats."""
        scenario_ticks = make_scenario_ticks(SYMBOL, [make_tick(1000)])
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, 'UNKNOWN', 2.0)

        assert stats.ticks_total == 0
        assert stats.budget_ms == 2.0


# =============================================================================
# CLIPPING STATS
# =============================================================================

class TestClippingStats:
    """ClippingStats correctness."""

    def test_clipping_rate_calculation(self, preparator, sparse_ticks):
        """Clipping rate must match ticks_clipped / ticks_total * 100."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        _, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        expected_rate = round(stats.ticks_clipped / stats.ticks_total * 100, 2)
        assert stats.clipping_rate_pct == expected_rate

    def test_stats_sum_invariant(self, preparator, sparse_ticks):
        """ticks_kept + ticks_clipped must equal ticks_total."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        _, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert stats.ticks_kept + stats.ticks_clipped == stats.ticks_total

    def test_zero_clipping_rate_when_none_clipped(self, preparator):
        """Rate must be 0.0 when all ticks pass."""
        ticks = [make_tick(1000), make_tick(2000)]  # 1000ms gap, any budget passes
        scenario_ticks = make_scenario_ticks(SYMBOL, ticks)
        _, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 5.0)

        assert stats.ticks_clipped == 0
        assert stats.clipping_rate_pct == 0.0

    def test_budget_recorded_in_stats(self, preparator, regular_ticks):
        """ClippingStats must record the budget value used."""
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        _, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 3.14)

        assert stats.budget_ms == 3.14
