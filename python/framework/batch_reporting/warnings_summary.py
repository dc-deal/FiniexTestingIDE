"""
FiniexTestingIDE - Warnings Summary
Global warnings and notices for batch execution results

Consolidates all system-wide warnings (stress tests, data quality, etc.)
into a single section. Always rendered regardless of summary_detail setting.
"""

from typing import Any, Dict, Optional

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.trading_env_types.stress_test_types import StressTestConfig
from python.framework.utils.console_renderer import ConsoleRenderer


class WarningsSummary(AbstractBatchSummarySection):
    """
    Global warnings and notices section.

    Renders only when at least one warning is active.
    Always displayed regardless of summary_detail flag.
    """

    _section_title = '⚠️ WARNINGS & NOTICES'

    def __init__(
        self,
        batch_execution_summary: BatchExecutionSummary,
        profiling_data_map: Optional[Dict[Any, Any]] = None
    ):
        """
        Initialize warnings summary.

        Args:
            batch_execution_summary: Batch execution results
            profiling_data_map: Pre-built profiling data (avoids pop-mutation issue)
        """
        self._batch_summary = batch_execution_summary
        self._profiling_data_map = profiling_data_map or {}

    def render(self, renderer: ConsoleRenderer) -> None:
        """
        Render all active warnings. Skips section entirely if none are active.

        Args:
            renderer: Console renderer for formatting
        """
        # Collect all warning blocks
        warning_blocks = []

        stress_test_block = self._build_stress_test_warning(renderer)
        if stress_test_block:
            warning_blocks.append(stress_test_block)

        validation_block = self._build_validation_warning(renderer)
        if validation_block:
            warning_blocks.append(validation_block)

        data_version_block = self._build_data_version_warning(renderer)
        if data_version_block:
            warning_blocks.append(data_version_block)

        budget_block = self._build_budget_warning(renderer)
        if budget_block:
            warning_blocks.append(budget_block)

        granularity_block = self._build_budget_granularity_warning(renderer)
        if granularity_block:
            warning_blocks.append(granularity_block)

        too_high_block = self._build_budget_too_high_warning(renderer)
        if too_high_block:
            warning_blocks.append(too_high_block)

        # Only render section if there are warnings
        if not warning_blocks:
            return

        self._render_section_header(renderer)

        for i, block in enumerate(warning_blocks):
            print(block)
            if i < len(warning_blocks) - 1:
                print()

    def _build_stress_test_warning(self, renderer: ConsoleRenderer) -> str:
        """
        Build stress test warning block if any scenario has active stress tests.

        Args:
            renderer: Console renderer for formatting

        Returns:
            Formatted warning string or empty string
        """
        scenarios = self._batch_summary.single_scenario_list

        # Group scenarios by stress test config signature
        config_groups: dict[str, list[str]] = {}
        for scenario in scenarios:
            config = StressTestConfig.from_dict(scenario.stress_test_config)
            if not config.has_any_enabled():
                continue
            parts = []
            if config.reject_open_order and config.reject_open_order.enabled:
                ro = config.reject_open_order
                parts.append(
                    f"reject_open_order: probability={ro.probability:.0%}, seed={ro.seed}")
            signature = ' | '.join(parts)
            if signature not in config_groups:
                config_groups[signature] = []
            config_groups[signature].append(scenario.name)

        if not config_groups:
            return ''

        lines = []
        lines.append(renderer.red(
            'STRESS TEST ACTIVE — Results contain INTENTIONAL errors and rejections!'))
        for signature, scenario_names in config_groups.items():
            lines.append(renderer.yellow(
                f"  → {signature}"))
            lines.append(renderer.yellow(
                f"    Scenarios ({len(scenario_names)}): {', '.join(scenario_names)}"))

        return '\n'.join(lines)

    def _build_validation_warning(self, renderer: ConsoleRenderer) -> str:
        """
        Build validation warning block from scenario validation results.

        Args:
            renderer: Console renderer for formatting

        Returns:
            Formatted warning string or empty string
        """
        warning_count = 0
        scenarios_with_warnings = 0

        for scenario in self._batch_summary.single_scenario_list:
            scenario_warnings = sum(
                len(r.warnings) for r in scenario.validation_result if r.is_valid
            )
            if scenario_warnings > 0:
                warning_count += scenario_warnings
                scenarios_with_warnings += 1

        if warning_count == 0:
            return ''

        return renderer.yellow(
            f"Validation: {warning_count} warning(s) across "
            f"{scenarios_with_warnings} scenario(s) — see global log for details"
        )

    def _build_data_version_warning(self, renderer: ConsoleRenderer) -> str:
        """
        Build data format version warning if pre-V1.3.0 data is present.

        Args:
            renderer: Console renderer for formatting

        Returns:
            Formatted warning string or empty string
        """
        total_files = 0
        pre_v130_files = 0

        for scenario in self._batch_summary.single_scenario_list:
            for version in scenario.data_format_versions:
                total_files += 1
                # 'unknown' or any non-semver string treated as pre-V1.3.0
                if not version.startswith('1.') or version < '1.3.0':
                    pre_v130_files += 1

        if pre_v130_files == 0:
            return ''

        lines = []
        lines.append(renderer.yellow(
            f"Data includes pre-V1.3.0 files ({pre_v130_files}/{total_files}): "
            f"inter-tick intervals based on synthesized collected_msc"))

        # Kraken-specific caveat: synthetic 1ms spacing dominates interval statistics
        has_kraken = any(
            'kraken' in s.data_broker_type
            for s in self._batch_summary.single_scenario_list
            if s.data_format_versions and any(
                not v.startswith('1.') or v < '1.3.0'
                for v in s.data_format_versions
            )
        )
        if has_kraken:
            lines.append(renderer.yellow(
                '  → Kraken trade fills: 1ms spacing is synthetic — real arrival cadence unknown'))

        return '\n'.join(lines)

    def _build_budget_warning(self, renderer: ConsoleRenderer) -> str:
        """
        Build tick processing budget notice when avg processing exceeds P5 interval.

        Uses pre-built profiling_data_map to avoid pop-mutation issues with profile_times.

        Args:
            renderer: Console renderer for formatting

        Returns:
            Formatted warning string or empty string
        """
        # When budget is already active, clipping is being simulated — warning is redundant
        has_budget_active = bool(self._batch_summary.clipping_stats_map)
        if has_budget_active:
            return ''

        warning_count = 0

        for result in self._batch_summary.process_result_list:
            profiling = self._profiling_data_map.get(result.scenario_index)
            if not profiling or not profiling.interval_stats:
                continue

            ticks = result.tick_loop_results.coordination_statistics.ticks_processed
            if ticks == 0:
                continue
            avg_ms = profiling.total_per_tick_ms / ticks

            if avg_ms > profiling.interval_stats.p5_ms:
                warning_count += 1

        if warning_count == 0:
            return ''

        return renderer.yellow(
            f"Tick processing budget: {warning_count} scenario(s) exceed P5 tick interval "
            f"— consider setting tick_processing_budget_ms (see Profiling Analysis)"
        )

    def _build_budget_granularity_warning(self, renderer: ConsoleRenderer) -> str:
        """
        Build warning when tick processing budget is active but below data granularity.

        Args:
            renderer: Console renderer for formatting

        Returns:
            Formatted warning string or empty string
        """
        clipping_map = self._batch_summary.clipping_stats_map
        if not clipping_map:
            return ''

        # Check if any budget is < 1.0ms and produced 0 clipping
        ineffective = [
            c for c in clipping_map.values()
            if c.budget_ms < 1.0 and c.ticks_clipped == 0 and c.ticks_total > 0
        ]
        if not ineffective:
            return ''

        budget_values = sorted(set(c.budget_ms for c in ineffective))
        budget_str = ', '.join(f'{b}ms' for b in budget_values)

        return renderer.yellow(
            f"Tick processing budget ({budget_str}) below data granularity — "
            f"no effect with integer-ms collected_msc (minimum effective: 1.0ms)"
        )

    def _build_budget_too_high_warning(self, renderer: ConsoleRenderer) -> str:
        """
        Build warning when tick processing budget exceeds 2× P95 processing time.

        Args:
            renderer: Console renderer for formatting

        Returns:
            Formatted warning string or empty string
        """
        clipping_map = self._batch_summary.clipping_stats_map
        if not clipping_map:
            return ''

        # Compute avg processing per scenario from profiling data
        avg_times = []
        for result in self._batch_summary.process_result_list:
            profiling = self._profiling_data_map.get(result.scenario_index)
            if not profiling:
                continue
            ticks = result.tick_loop_results.coordination_statistics.ticks_processed
            if ticks == 0:
                continue
            avg_times.append(profiling.total_per_tick_ms / ticks)

        if not avg_times:
            return ''

        # P95 of avg processing times
        avg_times_sorted = sorted(avg_times)
        p95_idx = min(int(len(avg_times_sorted) * 0.95), len(avg_times_sorted) - 1)
        p95_processing = avg_times_sorted[p95_idx]

        # Check if any budget exceeds 2× P95
        max_budget = max(c.budget_ms for c in clipping_map.values())
        if max_budget <= p95_processing * 2:
            return ''

        return renderer.yellow(
            f"Tick processing budget ({max_budget}ms) exceeds 2× P95 processing time "
            f"({p95_processing:.3f}ms) — ticks clipped unnecessarily, reducing simulation accuracy"
        )
