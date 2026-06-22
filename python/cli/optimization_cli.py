"""
FiniexTestingIDE - Parameter Optimization CLI

Run a parameter sweep (grid search over a base scenario set), then rank the recorded
combinations and view the parameter sensitivity.

Usage:
    python python/cli/optimization_cli.py run cautious_macd_grid.json
    python python/cli/optimization_cli.py report sweep_20260621_223000
    python python/cli/optimization_cli.py report sweep_20260621_223000 --objective net_pnl --top 5
"""

import argparse
import sys
import traceback

from python.framework.optimization.optimization_report import render_sweep_report
from python.framework.optimization.optimization_runner import OptimizationRunner


class OptimizationCli:
    """Command-line interface for the Parameter Optimization system."""

    def cmd_run(self, spec_file: str):
        """
        Run a parameter sweep from a spec.

        Args:
            spec_file: Sweep spec filename or path
        """
        sweep_id = OptimizationRunner().run(spec_file)
        print(f"\nSweep id: {sweep_id}")
        print(f"Report:   python python/cli/optimization_cli.py report {sweep_id}")

    def cmd_report(
        self,
        sweep_id: str,
        objective: str,
        minimize: bool,
        currency: str,
        top: int,
    ):
        """
        Rank a sweep's combinations and print the parameter sensitivity.

        Args:
            sweep_id: The sweep to report on
            objective: Ledger KPI column to rank by
            minimize: Rank ascending instead of descending
            currency: Restrict to this account currency (None = single-currency runs)
            top: How many top combinations to print
        """
        render_sweep_report(
            sweep_id,
            objective=objective,
            maximize=not minimize,
            objective_currency=currency,
            top_n=top,
        )


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Parameter Optimization CLI (grid sweep + ranking)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # RUN command
    # ─────────────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser('run', help='Run a parameter sweep from a spec')
    run_parser.add_argument('spec', help='Sweep spec filename or path (e.g. cautious_macd_grid.json)')

    # ─────────────────────────────────────────────────────────────────────────
    # REPORT command
    # ─────────────────────────────────────────────────────────────────────────
    report_parser = subparsers.add_parser(
        'report', help='Rank + sensitivity for a sweep from the run-results ledger')
    report_parser.add_argument('sweep_id', help='The sweep id to report on')
    report_parser.add_argument(
        '--objective', default='expectancy', help='KPI to rank by (default: expectancy)')
    report_parser.add_argument(
        '--minimize', action='store_true', help='Rank ascending (e.g. for max_drawdown)')
    report_parser.add_argument(
        '--currency', default=None, help='Restrict to this account currency')
    report_parser.add_argument(
        '--top', type=int, default=10, help='How many top combinations to print')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = OptimizationCli()

    try:
        if args.command == 'run':
            cli.cmd_run(args.spec)
        elif args.command == 'report':
            cli.cmd_report(
                args.sweep_id, args.objective, args.minimize, args.currency, args.top)

    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
