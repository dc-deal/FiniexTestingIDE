"""
FiniexTestingIDE - Post-Run Validator

Produces the batch-global advisory warnings (Tier 1) that can only be known AFTER execution —
debug-mode, stress-test, data-version, and the tick-processing-budget advisories (which need the
per-scenario profiling / clipping data). Runs once after the batch and appends run-scoped
`ValidationResult`s to `BatchExecutionSummary.batch_validation_result`.

This is the "lift": these checks used to be computed INLINE in the warnings renderer. The reporting
pipeline makes no decisions — the verdict ("does this warrant a warning?") lives here, the report only
reads the structured result. See docs/architecture/warnings_errors_tiers.md.
"""

from typing import Optional

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_performance_types import (
    EXPECTED_OPERATIONS, ProfilingData)
from python.framework.types.trading_env_types.stress_test_types import StressTestConfig
from python.framework.types.validation_types import ValidationResult

# Overhead verdict threshold — coordination overhead as a share of computation time.
_HIGH_OVERHEAD_RATIO = 0.5
# Infra-bottleneck verdict threshold — share of scenarios where a non-hot-path op dominated.
_BOTTLENECK_PCT = 15.0
# Time-divergence threshold — a currency group spanning more days than this gets an advisory.
_TIME_DIVERGENCE_DAYS = 30


class PostRunValidator:
    """Emits the post-run batch-global advisory warnings into the batch-level validation channel."""

    def __init__(self, batch: BatchExecutionSummary):
        """
        Initialize the post-run validator.

        Args:
            batch: The completed batch summary (scenarios, process results, clipping, profiling)
        """
        self._batch = batch

    def validate(self) -> None:
        """Run all post-run advisory checks; append a run-scoped ValidationResult per active warning."""
        self._check_debug_mode()
        self._check_stress_test()
        self._check_data_version()
        self._check_budget()
        self._check_budget_granularity()
        self._check_budget_too_high()
        self._check_coordination_overhead()
        self._check_bottlenecks()
        self._check_multi_currency()
        self._check_time_divergence()

    def _add(self, check: str, message: str) -> None:
        """Append a run-scoped advisory warning (is_valid=True) to the batch-level channel."""
        self._batch.add_batch_validation_result(
            ValidationResult(is_valid=True, scenario_name=check, warnings=[message]))

    def _check_debug_mode(self) -> None:
        """Prominent notice when the batch ran in debug / serial mode (timings unreliable)."""
        if not self._batch.debug_execution:
            return
        self._add('debug_mode', (
            'DEBUG MODE — debugger attached / DEBUG_MODE set\n'
            '   Execution is SERIAL (single process) with trace overhead.\n'
            '   ⏱️  TIMINGS IN THIS REPORT ARE NOT REPRESENTATIVE — '
            'use a non-debug run for performance numbers.'))

    def _check_stress_test(self) -> None:
        """Warn when any scenario has active stress tests (results contain intentional errors)."""
        config_groups: dict[str, list[str]] = {}
        for scenario in self._batch.single_scenario_list:
            config = StressTestConfig.from_dict(scenario.stress_test_config)
            if not config.has_any_enabled():
                continue
            parts = []
            if config.reject_open_order and config.reject_open_order.enabled:
                ro = config.reject_open_order
                parts.append(
                    f"reject_open_order: probability={ro.probability:.0%}, seed={ro.seed}")
            signature = ' | '.join(parts)
            config_groups.setdefault(signature, []).append(scenario.name)

        if not config_groups:
            return

        lines = ['STRESS TEST ACTIVE — Results contain INTENTIONAL errors and rejections!']
        for signature, scenario_names in config_groups.items():
            lines.append(f"  → {signature}")
            lines.append(f"    Scenarios ({len(scenario_names)}): {', '.join(scenario_names)}")
        self._add('stress_test', '\n'.join(lines))

    def _check_data_version(self) -> None:
        """Warn when pre-V1.3.0 data is present (inter-tick intervals from synthesized collected_msc)."""
        total_files = 0
        pre_v130_files = 0
        for scenario in self._batch.single_scenario_list:
            for version in scenario.data_format_versions:
                total_files += 1
                # 'unknown' or any non-semver string treated as pre-V1.3.0
                if not version.startswith('1.') or version < '1.3.0':
                    pre_v130_files += 1

        if pre_v130_files == 0:
            return

        lines = [
            f"Data includes pre-V1.3.0 files ({pre_v130_files}/{total_files}): "
            f"inter-tick intervals based on synthesized collected_msc"]

        # Kraken-specific caveat: synthetic 1ms spacing dominates interval statistics
        has_kraken = any(
            'kraken' in s.data_broker_type
            for s in self._batch.single_scenario_list
            if s.data_format_versions and any(
                not v.startswith('1.') or v < '1.3.0'
                for v in s.data_format_versions
            )
        )
        if has_kraken:
            lines.append(
                '  → Kraken trade fills: 1ms spacing is synthetic — real arrival cadence unknown')
        self._add('data_version', '\n'.join(lines))

    def _check_budget(self) -> None:
        """Warn when avg tick processing exceeds the P5 interval (consider setting a budget)."""
        # When budget is already active, clipping is being simulated — warning is redundant
        if self._batch.clipping_stats_map:
            return

        warning_count = 0
        for result in self._batch.process_result_list:
            profiling = self._profiling(result)
            if not profiling or not profiling.interval_stats:
                continue
            ticks = result.tick_loop_results.coordination_statistics.ticks_processed
            if ticks == 0:
                continue
            avg_ms = profiling.total_per_tick_ms / ticks
            if avg_ms > profiling.interval_stats.p5_ms:
                warning_count += 1

        if warning_count == 0:
            return
        self._add('budget', (
            f"Tick processing budget: {warning_count} scenario(s) exceed P5 tick interval "
            f"— consider setting tick_processing_budget_ms (see Profiling Analysis)"))

    def _check_budget_granularity(self) -> None:
        """Warn when an active budget is below data granularity (no effect with integer-ms collected_msc)."""
        clipping_map = self._batch.clipping_stats_map
        if not clipping_map:
            return

        ineffective = [
            c for c in clipping_map.values()
            if c.budget_ms < 1.0 and c.ticks_clipped == 0 and c.ticks_total > 0
        ]
        if not ineffective:
            return

        budget_values = sorted(set(c.budget_ms for c in ineffective))
        budget_str = ', '.join(f'{b}ms' for b in budget_values)
        self._add('budget_granularity', (
            f"Tick processing budget ({budget_str}) below data granularity — "
            f"no effect with integer-ms collected_msc (minimum effective: 1.0ms)"))

    def _check_budget_too_high(self) -> None:
        """Warn when an active budget exceeds 2x P95 processing time (ticks clipped unnecessarily)."""
        clipping_map = self._batch.clipping_stats_map
        if not clipping_map:
            return

        avg_times = []
        for result in self._batch.process_result_list:
            profiling = self._profiling(result)
            if not profiling:
                continue
            ticks = result.tick_loop_results.coordination_statistics.ticks_processed
            if ticks == 0:
                continue
            avg_times.append(profiling.total_per_tick_ms / ticks)

        if not avg_times:
            return

        avg_times_sorted = sorted(avg_times)
        p95_idx = min(int(len(avg_times_sorted) * 0.95), len(avg_times_sorted) - 1)
        p95_processing = avg_times_sorted[p95_idx]

        max_budget = max(c.budget_ms for c in clipping_map.values())
        if max_budget <= p95_processing * 2:
            return
        self._add('budget_too_high', (
            f"Tick processing budget ({max_budget}ms) exceeds 2× P95 processing time "
            f"({p95_processing:.3f}ms) — ticks clipped unnecessarily, reducing simulation accuracy"))

    def _check_coordination_overhead(self) -> None:
        """Warn when worker/decision coordination overhead exceeds 50% of computation (was an inline report verdict)."""
        high = []
        for result in self._batch.process_result_list:
            tlr = result.tick_loop_results
            if not tlr or not tlr.profiling_data:
                continue
            op_total = tlr.profiling_data.profile_times.get('worker_decision', 0.0)
            worker_exec = sum(w.worker_total_time_ms for w in (tlr.worker_statistics or []))
            decision = tlr.decision_statistics.decision_total_time_ms if tlr.decision_statistics else 0.0
            computation = worker_exec + decision
            overhead = max(0.0, op_total - computation)
            if computation > 0 and overhead / computation > _HIGH_OVERHEAD_RATIO:
                high.append(result.scenario_name)
        if high:
            self._add('coordination_overhead', (
                f"Coordination overhead exceeds {_HIGH_OVERHEAD_RATIO:.0%} of computation in "
                f"{len(high)} scenario(s): {', '.join(high)} — see the worker decision breakdown"))

    def _check_bottlenecks(self) -> None:
        """Warn when a non-hot-path (infra) operation is the dominant cost in many scenarios (was a report verdict)."""
        freq = {}
        scenarios = 0
        for result in self._batch.process_result_list:
            tlr = result.tick_loop_results
            if not tlr or not tlr.profiling_data:
                continue
            scenarios += 1
            op = self._bottleneck_operation(tlr.profiling_data)
            if op:
                freq[op] = freq.get(op, 0) + 1
        if scenarios == 0:
            return
        for op in sorted(freq):
            if op in EXPECTED_OPERATIONS:
                continue
            pct = freq[op] / scenarios * 100
            if pct >= _BOTTLENECK_PCT:
                self._add('bottleneck', (
                    f"Infrastructure operation '{op}' is the dominant cost in "
                    f"{freq[op]}/{scenarios} scenario(s) ({pct:.0f}%) — candidate for optimization"))

    @staticmethod
    def _bottleneck_operation(profiling_data) -> str:
        """The operation with the largest total time (the scenario's bottleneck), or '' if none."""
        ops = {n: t for n, t in profiling_data.profile_times.items() if n != 'total_per_tick'}
        return max(ops, key=ops.get) if ops else ''

    def _check_multi_currency(self) -> None:
        """Advisory when a batch mixes account currencies (cross-currency P&L is not summed)."""
        currencies = sorted({
            result.tick_loop_results.portfolio_stats.currency
            for result in self._batch.process_result_list
            if result.tick_loop_results and result.tick_loop_results.portfolio_stats})
        if len(currencies) > 1:
            self._add('multi_currency', (
                f"Multi-currency batch ({len(currencies)} currencies: {', '.join(currencies)}) — "
                f"cross-currency aggregation is not performed; each currency group shows P&L in its "
                f"own currency."))

    def _check_time_divergence(self) -> None:
        """Advisory when a currency group's scenarios span a large time range (aggregation unrealistic)."""
        groups = {}
        for result in self._batch.process_result_list:
            tlr = result.tick_loop_results
            if not tlr or not tlr.portfolio_stats:
                continue
            trs = tlr.tick_range_stats
            if trs and trs.first_tick_time and trs.last_tick_time:
                groups.setdefault(tlr.portfolio_stats.currency, []).extend(
                    [trs.first_tick_time, trs.last_tick_time])
        for currency in sorted(groups):
            dates = groups[currency]
            span_days = (max(dates) - min(dates)).days
            if span_days > _TIME_DIVERGENCE_DAYS:
                self._add('time_divergence', (
                    f"Time divergence: {currency} group scenarios span {span_days} days — aggregated "
                    f"P&L is statistical only, not portfolio-representative (market conditions / "
                    f"volatility / rates differ)."))

    def _profiling(self, result: ProcessResult) -> Optional[ProfilingData]:
        """Build typed ProfilingData for a scenario, or None when no profiling data exists."""
        if (not result.tick_loop_results or
                not result.tick_loop_results.profiling_data):
            return None
        pd = result.tick_loop_results.profiling_data
        return ProfilingData.from_dicts(
            pd.profile_times, pd.profile_counts,
            inter_tick_intervals_ms=pd.inter_tick_intervals_ms,
            gap_threshold_s=pd.gap_threshold_s,
            ticks_total=pd.ticks_total)
