"""
FiniexTestingIDE - Strategy Runner CLI
Command-line interface for batch strategy testing

Usage:
    python python/cli/strategy_runner_cli.py run eurusd_3_windows.json
    python python/cli/strategy_runner_cli.py list
    python python/cli/strategy_runner_cli.py list --full-details
"""

import argparse
import sys
import traceback

from python.scenario.scenario_set_finder import ScenarioSetFinder
from python.framework.utils.time_utils import format_duration

from python.framework.logging.bootstrap_logger import get_global_logger
from python.scenario.scenario_strategy_runner import run_strategy_test
vLog = get_global_logger()


class StrategyRunnerCLI:
    """
    Command-line interface for strategy testing

    Provides scenario set discovery and execution
    """

    def __init__(self):
        """Initialize CLI"""
        self._finder = ScenarioSetFinder()

    def cmd_run(self, scenario_set_json: str):
        """
        Run strategy test with specified scenario set

        Args:
            scenario_set_json: Config filename (e.g., 'eurusd_3_windows.json')
        """
        print("\n" + "="*80)
        print("🔬 Strategy Runner")
        print("="*80)
        print(f"Scenario Set: {scenario_set_json}")
        print("="*80 + "\n")

        run_strategy_test(scenario_set_json)

    def cmd_list(self, full_details: bool = False):
        """
        List available scenario sets

        Args:
            full_details: If True, load and validate all configs (slow)
        """
        if full_details:
            self._list_with_full_details()
        else:
            self._list_files_only()

    def _list_files_only(self):
        """Fast: List config filenames only"""
        files = self._finder.list_available_files()

        print("\n" + "="*80)
        print("📋 Available Scenario Sets")
        print("="*80)

        if not files:
            print("⚠️  No scenario set config files found")
            print(f"   Location: {self._finder._config_path}")
        else:
            for file_path in files:
                print(f"  • {file_path.name}")

        print("="*80)
        print(f"Total: {len(files)} config file(s)")
        print("\nUse 'list --full-details' for detailed information")
        print("="*80 + "\n")

    def _list_with_full_details(self):
        """Slow: Load all configs and show full metadata"""
        print("\n" + "="*80)
        print("📋 Available Scenario Sets (Full Details)")
        print("="*80)
        print("⏳ Loading and validating all configs...")
        print("="*80 + "\n")

        metadata_list = self._finder.list_all_with_details()

        if not metadata_list:
            print("⚠️  No valid scenario set config files found")
            print(f"   Location: {self._finder._config_path}")
            print("="*80 + "\n")
            return

        print("="*80)
        print(f"Total: {len(metadata_list)} valid config file(s)")
        print("="*80 + "\n")

        for metadata in metadata_list:
            print(f"📄 {metadata.filename}")
            print(f"   Name:      {metadata.scenario_set_name}")
            print(f"   Scenarios: {metadata.enabled_count} enabled")
            print(f"   Symbols:   {', '.join(metadata.symbols)}")

            # === TIME ANALYSIS ===
            time_parts = []
            if metadata.timespan_scenario_count > 0:
                duration_str = format_duration(metadata.total_timespan_seconds)
                time_parts.append(
                    f"{duration_str} across {metadata.timespan_scenario_count} scenario{'s' if metadata.timespan_scenario_count > 1 else ''}"
                )
            if metadata.tick_scenario_count > 0:
                time_parts.append(
                    f"{metadata.total_ticks:,} ticks across {metadata.tick_scenario_count} scenario{'s' if metadata.tick_scenario_count > 1 else ''}"
                )

            if time_parts:
                print(f"   In-Time:   {', '.join(time_parts)}")

            # === STRATEGY INFO ===
            logic_parts = []

            # Decision logic
            if metadata.is_mixed_decision_logic:
                logic_parts.append("Mixed")
            elif metadata.decision_logic_type:
                logic_parts.append(metadata.decision_logic_type)

            # Worker count
            if metadata.is_mixed_workers:
                logic_parts.append("Mixed Workers")
            elif metadata.worker_count is not None:
                worker_str = f"{metadata.worker_count} Worker{'s' if metadata.worker_count != 1 else ''}"
                logic_parts.append(worker_str)

            if logic_parts:
                print(
                    f"   Logic:     {' ('.join(logic_parts)}{')'if len(logic_parts) > 1 else ''}")

            print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Batch strategy testing CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # RUN command
    # ─────────────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        'run', help='Run strategy test with specified scenario set')
    run_parser.add_argument(
        'scenario_set', help='Scenario set config filename (e.g., eurusd_3_windows.json)')

    # ─────────────────────────────────────────────────────────────────────────
    # LIST command
    # ─────────────────────────────────────────────────────────────────────────
    list_parser = subparsers.add_parser(
        'list', help='List available scenario set files')
    list_parser.add_argument(
        '--full-details', action='store_true', default=False,
        help='Load and validate all configs (slow)')

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = StrategyRunnerCLI()

    try:
        if args.command == 'run':
            cli.cmd_run(args.scenario_set)

        elif args.command == 'list':
            cli.cmd_list(full_details=args.full_details)

    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
