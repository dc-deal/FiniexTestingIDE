"""
Tick Processing Budget — Flag-Based Filtering Tests
=====================================================
Tests for _apply_tick_budget() virtual clock algorithm.

Flag-based: all ticks are returned with is_clipped=True/False flag.
Broker path sees every tick; algo path skips clipped ticks.

Covers:
- Basic flagging with known tick sequences
- is_clipped flag correctness
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
        Result: 4 kept, 2 clipped — all 6 ticks returned with flags
        """
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert stats.ticks_total == 6
        assert stats.ticks_kept == 4
        assert stats.ticks_clipped == 2
        assert stats.budget_ms == 2.0

        # All ticks returned (flag-based, not removal-based)
        all_ticks = filtered['ticks'][SYMBOL]
        assert len(all_ticks) == 6

        # Verify is_clipped flags
        algo_msc = [t['collected_msc'] for t in all_ticks if not t['is_clipped']]
        clipped_msc = [t['collected_msc'] for t in all_ticks if t['is_clipped']]
        assert algo_msc == [1000, 1002, 1005, 1008]
        assert clipped_msc == [1001, 1003]

    def test_budget_1ms_integer_spacing(self, preparator, regular_ticks):
        """
        Budget 1ms with 1ms-spaced ticks.

        Every tick passes: tick[i].collected_msc = 1000+i,
        virtual_clock after tick[i] = 1000+i+1 = tick[i+1].collected_msc.
        All 10 ticks kept, none clipped.
        """
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 1.0)

        assert stats.ticks_total == 10
        assert stats.ticks_kept == 10
        assert stats.ticks_clipped == 0

        # All ticks returned, all with is_clipped=False
        all_ticks = filtered['ticks'][SYMBOL]
        assert len(all_ticks) == 10
        assert all(not t['is_clipped'] for t in all_ticks)

    def test_large_budget_clips_most(self, preparator, regular_ticks):
        """Budget 5ms with 1ms spacing — only every 5th tick survives algo path."""
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 5.0)

        # 1000: keep (clock→1005), 1005: keep (clock→1010) — only 2 algo
        assert stats.ticks_kept == 2
        assert stats.ticks_clipped == 8

        # All 10 ticks returned with flags
        all_ticks = filtered['ticks'][SYMBOL]
        assert len(all_ticks) == 10

        algo_msc = [t['collected_msc'] for t in all_ticks if not t['is_clipped']]
        assert algo_msc == [1000, 1005]

    def test_first_tick_always_kept(self, preparator):
        """First tick always passes (virtual_clock starts at 0)."""
        ticks = [make_tick(500)]
        scenario_ticks = make_scenario_ticks(SYMBOL, ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 999.0)

        assert stats.ticks_kept == 1
        assert stats.ticks_clipped == 0

        all_ticks = filtered['ticks'][SYMBOL]
        assert len(all_ticks) == 1
        assert not all_ticks[0]['is_clipped']

    def test_budget_preserves_ranges(self, preparator, sparse_ticks):
        """Filtering must preserve the original 'ranges' dict."""
        ranges = {SYMBOL: ('2026-01-01', '2026-01-02')}
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        scenario_ticks['ranges'] = ranges

        filtered, _ = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert filtered['ranges'] is ranges

    def test_counts_reflect_total_ticks(self, preparator, sparse_ticks):
        """Counts dict reflects total tick count (all ticks kept with flags)."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        # counts = total (not just algo ticks)
        assert filtered['counts'][SYMBOL] == stats.ticks_total
        # All ticks returned
        assert len(filtered['ticks'][SYMBOL]) == stats.ticks_total
        # Algo tick count matches stats
        algo_count = sum(1 for t in filtered['ticks'][SYMBOL] if not t['is_clipped'])
        assert algo_count == stats.ticks_kept


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
        """Pre-V1.3.0 data (collected_msc=0) skips flagging, returns unchanged."""
        scenario_ticks = make_scenario_ticks(SYMBOL, pre_v13_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        assert stats.ticks_total == 3
        assert stats.ticks_kept == 3
        assert stats.ticks_clipped == 0
        assert stats.budget_ms == 2.0

        # All original ticks preserved (no is_clipped flag — data returned unchanged)
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
        All ticks pass — all flagged is_clipped=False.
        """
        scenario_ticks = make_scenario_ticks(SYMBOL, regular_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 0.3)

        assert stats.ticks_total == 10
        assert stats.ticks_kept == 10
        assert stats.ticks_clipped == 0

        all_ticks = filtered['ticks'][SYMBOL]
        assert all(not t['is_clipped'] for t in all_ticks)

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


# =============================================================================
# FLAG-BASED TICK SPLIT
# =============================================================================

class TestFlagBasedSplit:
    """Tests for is_clipped flag correctness and tick preservation."""

    def test_all_ticks_returned_with_flags(self, preparator, sparse_ticks):
        """All ticks returned regardless of clipping — flags control algo path."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        all_ticks = filtered['ticks'][SYMBOL]
        # All 6 original ticks present
        assert len(all_ticks) == 6
        # Every tick has is_clipped flag
        assert all('is_clipped' in t for t in all_ticks)

    def test_flag_values_match_virtual_clock(self, preparator, sparse_ticks):
        """is_clipped flags exactly match virtual clock algorithm."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, _ = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        all_ticks = filtered['ticks'][SYMBOL]
        flags = [t['is_clipped'] for t in all_ticks]
        # 1000: False, 1001: True, 1002: False, 1003: True, 1005: False, 1008: False
        assert flags == [False, True, False, True, False, False]

    def test_original_tick_data_preserved(self, preparator, sparse_ticks):
        """Flagging must not alter original tick fields (bid, ask, time_msc)."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, _ = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        all_ticks = filtered['ticks'][SYMBOL]
        for i, tick in enumerate(all_ticks):
            assert tick['bid'] == sparse_ticks[i]['bid']
            assert tick['ask'] == sparse_ticks[i]['ask']
            assert tick['collected_msc'] == sparse_ticks[i]['collected_msc']
            assert tick['time_msc'] == sparse_ticks[i]['time_msc']

    def test_tick_dicts_are_copies(self, preparator, sparse_ticks):
        """Flagged ticks must be copies — original dicts must not be mutated."""
        originals = [dict(t) for t in sparse_ticks]
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        # Original dicts should NOT have is_clipped key
        for orig in originals:
            assert 'is_clipped' not in orig

    def test_algo_tick_count_equals_stats_kept(self, preparator, sparse_ticks):
        """Number of non-clipped ticks must equal stats.ticks_kept."""
        scenario_ticks = make_scenario_ticks(SYMBOL, sparse_ticks)
        filtered, stats = preparator._apply_tick_budget(scenario_ticks, SYMBOL, 2.0)

        all_ticks = filtered['ticks'][SYMBOL]
        algo_count = sum(1 for t in all_ticks if not t['is_clipped'])
        clipped_count = sum(1 for t in all_ticks if t['is_clipped'])

        assert algo_count == stats.ticks_kept
        assert clipped_count == stats.ticks_clipped
        assert algo_count + clipped_count == stats.ticks_total
