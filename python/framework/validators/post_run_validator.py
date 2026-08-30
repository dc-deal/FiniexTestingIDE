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

from python.framework.reporting.builders.robustness_report_builder import (
    build_robustness_report_from_batch,
)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_performance_types import (
    EXPECTED_OPERATIONS,
    ProfilingData,
)
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.utils.version_utils import parse_version
from python.framework.validators.shared_advisory_checks import check_stress_test

# Scope of a batch-global finding — it concerns the run, not one scenario.
_RUN_SCOPE = 'run'
# What a unit is called in the shared stress-test message (live says 'Session').
_SCENARIO_UNIT_LABEL = 'Scenarios'
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
        self._check_parallel_penalty()
        self._check_multi_currency()
        self._check_time_divergence()
        self._check_robustness()

    def _add(self, check: str, domain: ValidationDomain, message: str) -> None:
        """
        Append a run-scoped advisory finding to the batch-level channel.

        Args:
            check: Stable identifier of the assertion that produced the finding
            domain: The area it belongs to
            message: Operator-readable text
        """
        self._add_finding(ValidationFinding(
            severity=Severity.WARNING, check=check, domain=domain, message=message,
            scope=_RUN_SCOPE))

    def _add_finding(self, finding: ValidationFinding) -> None:
        """
        Append one already-built advisory finding to the batch-level channel.

        Args:
            finding: The advisory finding to record (from a shared check)
        """
        self._batch.add_batch_validation_result(ValidationResult(_RUN_SCOPE, [finding]))

    def _check_debug_mode(self) -> None:
        """Prominent notice when the batch ran in debug / serial mode (timings unreliable)."""
        if not self._batch.debug_execution:
            return
        self._add('debug_mode', ValidationDomain.SETUP, (
            'DEBUG MODE — debugger attached / DEBUG_MODE set\n'
            '   Execution is SERIAL (single process) with trace overhead.\n'
            '   ⏱️  TIMINGS IN THIS REPORT ARE NOT REPRESENTATIVE — '
            'use a non-debug run for performance numbers.'))

    def _check_stress_test(self) -> None:
        """Warn when any scenario has active stress tests (shared with the live session check)."""
        finding = check_stress_test(
            [(s.name, s.stress_test_config) for s in self._batch.single_scenario_list],
            _SCENARIO_UNIT_LABEL)
        if finding is not None:
            self._add_finding(finding)

    def _check_data_version(self) -> None:
        """
        Warn when the tick index carries no data format version for a file.

        Deliberately makes no claim about the data itself: data_format_version is an
        operator-set collector input declaring a schema, not a record of how a field was
        obtained (a collector that starts recording collected_msc without a version bump
        is invisible in it). Only the absence of the field is a fact this can state.
        """
        total_files = 0
        unknown_files = 0
        for scenario in self._batch.single_scenario_list:
            for version in scenario.data_format_versions:
                total_files += 1
                if parse_version(version) is None:
                    unknown_files += 1

        if unknown_files == 0:
            return

        self._add('data_version_unknown', ValidationDomain.DATA, (
            f'Data format version unknown for {unknown_files}/{total_files} file(s) — '
            f'the tick index carries no version for them\n'
            f'  → If the index predates the version field, rebuild it:\n'
            f'    python python/cli/tick_index_cli.py rebuild'))

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
        self._add('budget', ValidationDomain.PROFILING, (
            f'Tick processing budget: {warning_count} scenario(s) exceed P5 tick interval '
            f'— consider setting tick_processing_budget_ms (see Profiling Analysis)'))

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
        self._add('budget_granularity', ValidationDomain.PROFILING, (
            f'Tick processing budget ({budget_str}) below data granularity — '
            f'no effect with integer-ms collected_msc (minimum effective: 1.0ms)'))

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
        self._add('budget_too_high', ValidationDomain.PROFILING, (
            f'Tick processing budget ({max_budget}ms) exceeds 2× P95 processing time '
            f'({p95_processing:.3f}ms) — ticks clipped unnecessarily, reducing simulation accuracy'))

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
            self._add('coordination_overhead', ValidationDomain.PERFORMANCE, (
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
                self._add('bottleneck', ValidationDomain.PERFORMANCE, (
                    f"Infrastructure operation '{op}' is the dominant cost in "
                    f"{freq[op]}/{scenarios} scenario(s) ({pct:.0f}%) — candidate for optimization"))

    @staticmethod
    def _bottleneck_operation(profiling_data) -> str:
        """The operation with the largest total time (the scenario's bottleneck), or '' if none."""
        ops = {n: t for n, t in profiling_data.profile_times.items() if n != 'total_per_tick'}
        return max(ops, key=ops.get) if ops else ''

    def _check_parallel_penalty(self) -> None:
        """Warn when parallel worker execution COST time instead of saving it."""
        penalised = [
            (result.scenario_name, result.tick_loop_results.coordination_statistics)
            for result in self._batch.process_result_list
            if result.tick_loop_results
            and result.tick_loop_results.coordination_statistics
            and result.tick_loop_results.coordination_statistics.parallel_workers
            and result.tick_loop_results.coordination_statistics.parallel_time_saved_ms < 0
        ]
        if not penalised:
            return
        name, stats = min(penalised, key=lambda pair: pair[1].parallel_time_saved_ms)
        self._add('parallel_penalty', ValidationDomain.PERFORMANCE, (
            f'Parallel worker execution lost {abs(stats.parallel_time_saved_ms):.1f}ms in '
            f"'{name}'{f' (+{len(penalised) - 1} more)' if len(penalised) > 1 else ''} — "
            f'consider disabling parallel workers for this workload'))

    def _check_multi_currency(self) -> None:
        """Advisory when a batch mixes account currencies (cross-currency P&L is not summed)."""
        currencies = sorted({
            result.tick_loop_results.portfolio_stats.currency
            for result in self._batch.process_result_list
            if result.tick_loop_results and result.tick_loop_results.portfolio_stats})
        if len(currencies) > 1:
            self._add('multi_currency', ValidationDomain.PORTFOLIO, (
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
                self._add('time_divergence', ValidationDomain.DATA, (
                    f'Time divergence: {currency} group scenarios span {span_days} days — aggregated '
                    f'P&L is statistical only, not portfolio-representative (market conditions / '
                    f'volatility / rates differ).'))

    def _check_robustness(self) -> None:
        """Robustness verdict (#367) — OVERFIT / param-drift / low-N advisories, gated on trust."""
        config = self._batch.robustness_config
        if not config.enabled:
            return
        report = build_robustness_report_from_batch(self._batch)
        if report.distribution is None:
            return

        # Parameter drift always invalidates the comparison (fair-test prerequisite).
        if not report.params_constant:
            self._add('robustness_param_drift', ValidationDomain.ROBUSTNESS, (
                f"ROBUSTNESS: parameters differ across {len(report.drifting_windows)} window(s) "
                f"({', '.join(report.drifting_windows)}) — the IS/OOS comparison + distribution "
                f"are not a fair test (hold the strategy constant across windows)."))

        # Too few windows → the distribution is statistically weak.
        if report.distribution.window_count < config.min_windows:
            self._add('robustness_low_windows', ValidationDomain.ROBUSTNESS, (
                f'ROBUSTNESS: only {report.distribution.window_count} window(s) '
                f'(< {config.min_windows}) — the distribution is statistically weak; add more '
                f'windows before trusting it.'))

        # Trust gate: block-splitting distortion makes the per-window numbers artifacts.
        if report.disposition_pct > config.disposition_trust_pct:
            self._add('robustness_low_trust', ValidationDomain.ROBUSTNESS, (
                f'ROBUSTNESS: verdict suppressed — block-splitting distortion '
                f'{report.disposition_pct:.1f}% exceeds {config.disposition_trust_pct:.0f}%; the '
                f'per-window numbers are artifacts (use continuous mode / larger blocks).'))
            return  # numbers unreliable → no OVERFIT/ROBUST verdict

        # Per-bucket sufficiency: the WFE rests on BOTH the IS and OOS means. The overall
        # window check above can pass while a single bucket — usually OOS — is decimated
        # (excluded / crashed scenarios), so guard each bucket before trusting the verdict.
        is_n = report.in_sample.window_count if report.in_sample else 0
        oos_n = report.out_of_sample.window_count if report.out_of_sample else 0
        if is_n < config.min_windows or oos_n < config.min_windows:
            self._add('robustness_insufficient_buckets', ValidationDomain.ROBUSTNESS, (
                f'ROBUSTNESS: verdict suppressed — IS={is_n} / OOS={oos_n} window(s), one below '
                f'the {config.min_windows}-window minimum; the Walk-Forward Efficiency rests on '
                f'too few windows to trust (add windows / recover excluded scenarios).'))
            return  # a bucket too small → no OVERFIT/ROBUST verdict

        # The degradation verdict — only OVERFIT fires a warning (ROBUST is good news, no advisory).
        wfe = report.walk_forward_efficiency
        if wfe is not None and wfe < config.overfit_wfe_threshold:
            self._add('robustness_overfit', ValidationDomain.ROBUSTNESS, (
                f'ROBUSTNESS: OVERFIT — Walk-Forward Efficiency {wfe:.2f} (OOS/IS) below '
                f'{config.overfit_wfe_threshold:.2f}; out-of-sample performance degrades sharply '
                f'from in-sample (likely curve-fit to the IS windows).'))

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
