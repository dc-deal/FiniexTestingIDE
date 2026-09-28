"""
FiniexTestingIDE - Strategy Runner CLI
Command-line interface for batch strategy testing

Usage:
    python python/cli/strategy_runner_cli.py run eurusd_3_windows.json
    python python/cli/strategy_runner_cli.py validate [file ...]

Listing what can run is `config_directory_cli.py list` (#554) — a lean CLI, because this one
imports the whole batch pipeline.
"""

import argparse
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from python.framework.config_directory.config_directory_validation import validate_scenario_sets
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.console.config_directory_summary import render_config_validation
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.run_origin_types import RunChannel
from python.framework.types.run_outcome_types import RunOutcome
from python.scenario.scenario_strategy_runner import run_profile_batch, run_scenario_batch

vLog = get_global_logger()


class StrategyRunnerCli:
    """
    Command-line interface for strategy testing

    Runs scenario sets and validates them; listing them is the config directory's job (#554)
    """

    def cmd_run(
        self,
        scenario_set_json: str,
        generator_profiles: List[str] = None,
    ) -> Optional[BatchExecutionSummary]:
        """
        Run batch execution with specified scenario set.

        Args:
            scenario_set_json: Config filename (e.g., 'eurusd_3_windows.json')
            generator_profiles: Optional profile paths/directories for Profile Run

        Returns:
            The batch summary, or None when the run failed before producing one (#372)
        """
        if generator_profiles:
            profile_paths = self._resolve_profile_paths(generator_profiles)

            print('\n' + '='*80)
            print('🔬 Strategy Runner — Profile Run')
            print('='*80)
            print(f'Scenario Set: {scenario_set_json}')
            print(f'Profiles:     {len(profile_paths)} file(s)')
            for p in profile_paths:
                print(f'  • {Path(p).name}')
            print('='*80 + '\n')

            return run_profile_batch(scenario_set_json, profile_paths, channel=RunChannel.CLI)
        else:
            print('\n' + '='*80)
            print('🔬 Strategy Runner')
            print('='*80)
            print(f'Scenario Set: {scenario_set_json}')
            print('='*80 + '\n')

            return run_scenario_batch(scenario_set_json, channel=RunChannel.CLI)

    def _resolve_profile_paths(self, inputs: List[str]) -> List[str]:
        """
        Resolve profile inputs to file paths. Accepts files and directories.

        Args:
            inputs: List of file paths or directory paths

        Returns:
            Sorted list of resolved profile JSON file paths
        """
        resolved = []
        for entry in inputs:
            path = Path(entry)
            if path.is_dir():
                # Recursive — profiles are stored under <mode>/<broker_type>/, so a directory
                # at any level (mode, broker, or flat) discovers all profiles beneath it.
                json_files = sorted(path.rglob('*.json'))
                if not json_files:
                    raise FileNotFoundError(
                        f'No JSON profile files found in directory: {path}'
                    )
                resolved.extend(str(f) for f in json_files)
            elif path.is_file():
                resolved.append(str(path))
            else:
                raise FileNotFoundError(f'Profile path not found: {path}')
        return resolved

    def cmd_validate(self, files: Optional[List[str]] = None) -> None:
        """
        Run the loader over the scenario sets the config directory lists.

        Args:
            files: Only these file names; None validates every scenario set
        """
        render_config_validation(validate_scenario_sets(files))


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Backtesting CLI',
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
    run_parser.add_argument(
        '--generator-profile',
        type=str,
        nargs='+',
        default=None,
        help='Generator-profile JSON file(s) or directories for a Profile Run'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # VALIDATE command
    # ─────────────────────────────────────────────────────────────────────────
    validate_parser = subparsers.add_parser(
        'validate', help='Run the loader over the scenario sets the config directory lists')
    validate_parser.add_argument(
        'files', nargs='*', help='Only these file names (default: every scenario set)')

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = StrategyRunnerCli()

    try:
        if args.command == 'run':
            summary = cli.cmd_run(
                args.scenario_set, generator_profiles=args.generator_profile)
            # No summary means the batch never ran — a dead process, not a clean one (#372)
            sys.exit(summary.get_exit_code() if summary
                     else RunOutcome.CRASHED.get_exit_code())

        elif args.command == 'validate':
            cli.cmd_validate(args.files or None)

    except KeyboardInterrupt:
        print('\n\n👋 Interrupted by user')
        sys.exit(0)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
