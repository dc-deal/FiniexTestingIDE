"""
FiniexTestingIDE - Warnings Summary
Global warnings and notices for batch execution results

Consolidates all system-wide warnings (stress tests, data quality, etc.)
into a single section. Always rendered regardless of summary_detail setting.
"""

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

    def __init__(self, batch_execution_summary: BatchExecutionSummary):
        """
        Initialize warnings summary.

        Args:
            batch_execution_summary: Batch execution results
        """
        self._batch_summary = batch_execution_summary

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

        data_version_block = self._build_data_version_warning(renderer)
        if data_version_block:
            warning_blocks.append(data_version_block)

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
