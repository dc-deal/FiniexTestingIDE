"""
FiniexTestingIDE - Behaviour Comparison CLI (#641)

The same fixed backtest run on two code stands — what runs now and what is to be deployed — to
see whether what a strategy does has changed, not only whether its code did. Each stand is a
framework commit and, optionally, a strategy commit; each runs from a throwaway tree against a
scratch data home, and everything built is removed again.

Usage:
    python python/cli/behaviour_comparison_cli.py compare --scenario-set <path> \\
        --old-framework <commit> --new-framework <commit>
    python python/cli/behaviour_comparison_cli.py compare --scenario-set <path> \\
        --old-framework <commit> --new-framework <commit> \\
        --strategy-repo <path> --old-strategy <commit> --new-strategy <commit>

Exit codes: 0 nothing executable changed, or the two runs' records are IDENTICAL or RESHAPED ·
3 the result is DIFFERENT · 2 refused, or a stand could not be built, ran no backtest or loaded
other code · 1 an error · 130 interrupted. A stand whose own backtest finished with logged errors
counts as run: it computed its events, and its records are compared.
"""

import argparse
import sys
import traceback
from pathlib import Path
from typing import Optional

from python.framework.behaviour_comparison.behaviour_comparison_coordinator import (
    BehaviourComparisonCoordinator,
)
from python.framework.reporting.console.behaviour_comparison_console import (
    render_behaviour_comparison,
)
from python.framework.validators.behaviour_comparison_validator import comparison_exit_code


class BehaviourComparisonCli:
    """Command-line interface for the behaviour comparison."""

    def cmd_compare(self, scenario_set: str, old_framework: str, new_framework: str,
                    framework_repo: Optional[str], strategy_repo: Optional[str],
                    old_strategy: Optional[str], new_strategy: Optional[str]) -> int:
        """
        Run one comparison and print it.

        Args:
            scenario_set: The scenario set both stands run
            old_framework: The framework revision that runs now
            new_framework: The framework revision that is to be deployed
            framework_repo: The framework repository; None for this tree's
            strategy_repo: The strategy repository; None where no strategy revision is given
            old_strategy: The strategy revision that runs now
            new_strategy: The strategy revision that is to be deployed

        Returns:
            The exit code
        """
        comparison = BehaviourComparisonCoordinator(
            scenario_set=Path(scenario_set).absolute(),
            old_revisions=(old_framework, old_strategy),
            new_revisions=(new_framework, new_strategy),
            framework_root=str(Path(framework_repo).absolute()) if framework_repo else None,
            strategy_root=str(Path(strategy_repo).absolute()) if strategy_repo else None).run()
        print(render_behaviour_comparison(comparison))
        return comparison_exit_code(comparison)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Run one fixed backtest on two code stands and say whether they both ran',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    compare_parser = subparsers.add_parser(
        'compare', help='Run a scenario set on the old and on the new stand')
    compare_parser.add_argument('--scenario-set', required=True,
                                help='The scenario set both stands run, as a file path')
    compare_parser.add_argument('--old-framework', required=True,
                                help='The framework revision that runs now')
    compare_parser.add_argument('--new-framework', required=True,
                                help='The framework revision that is to be deployed')
    compare_parser.add_argument('--framework-repo', default=None,
                                help="The framework repository; this tree's when omitted")
    compare_parser.add_argument('--strategy-repo', default=None,
                                help='The strategy repository, below the data home')
    compare_parser.add_argument('--old-strategy', default=None,
                                help='The strategy revision that runs now')
    compare_parser.add_argument('--new-strategy', default=None,
                                help='The strategy revision that is to be deployed')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = BehaviourComparisonCli()

    try:
        if args.command == 'compare':
            sys.exit(cli.cmd_compare(args.scenario_set, args.old_framework, args.new_framework,
                                     args.framework_repo, args.strategy_repo, args.old_strategy,
                                     args.new_strategy))

    except KeyboardInterrupt:
        print('\n\n⚠️  Interrupted by user')
        sys.exit(130)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
