"""
FiniexTestingIDE - Divergence Report CLI (#512)

A live deployment held against backtests of its windows: the live side read from its records, each
segment's window backtested from what its sessions recorded, the gate, the measures, the venue's own
record, and the verdict by the rules fixed before the run. The report is kept under
`runs/divergence/<report_id>/` and printed.

Usage:
    python python/cli/divergence_report_cli.py compare --deployment <id> --decision-timeframe D1
    python python/cli/divergence_report_cli.py compare --deployment <id> --decision-timeframe D1 \\
        --until 2026-12-24T00:00:00Z --records <copy of the bot host's data home>
    python python/cli/divergence_report_cli.py compare --deployment <id> --decision-timeframe D1 \\
        --reading rehearsal --declared-stop 2026-12-05T10:00:00Z,changed,sold by hand

Exit codes: 0 every segment compared and passed · 2 a segment not compared (archive, backtest or
gate) · 3 a compared segment failed or was not checked · 1 an error.
"""

import argparse
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.divergence_report_coordinator import DivergenceReportCoordinator
from python.framework.types.api.divergence_report_types import DeclaredStop, DivergenceReading
from python.framework.utils.time_utils import parse_datetime
from python.framework.validators.divergence_verdict_validator import exit_code_of


class DivergenceReportCli:
    """Command-line interface for the divergence report."""

    def cmd_compare(self, deployment: str, decision_timeframe: str, reading: str,
                    until: Optional[str], records: Optional[str], declared_stops: List[str],
                    ledger_credentials: str) -> int:
        """
        Compare a deployment with backtests of its windows and keep the report.

        Args:
            deployment: The deployment id
            decision_timeframe: The timeframe whose bar's close is a day's decision
            reading: 'run' or 'rehearsal'
            until: The boundary the comparison ends at, ISO; None for the last one sealed
            records: A copy of the bot host's data home; None for this machine's stores
            declared_stops: Each declared stop as '<instant>,<changed|unchanged>[,<note>]'
            ledger_credentials: The configured name of the key that may read the venue's ledger

        Returns:
            The exit code
        """
        coordinator = DivergenceReportCoordinator(
            deployment_id=deployment, decision_timeframe=decision_timeframe,
            reading=DivergenceReading(reading), logger=get_global_logger(),
            until=parse_datetime(until) if until else None,
            records_root=Path(records) if records else None,
            declared_stops=[_declared_stop(text) for text in declared_stops],
            ledger_credentials=ledger_credentials)
        report, text, report_dir = coordinator.run()
        print(text)
        print(f'Kept in {report_dir}')
        return exit_code_of(report.segments)


def _declared_stop(text: str) -> DeclaredStop:
    """
    A declared stop as the command line names it.

    Args:
        text: '<instant>,<changed|unchanged>[,<note>]'

    Returns:
        The stop
    """
    parts = text.split(',', 2)
    if len(parts) < 2 or parts[1] not in ('changed', 'unchanged'):
        raise ValueError(f"--declared-stop '{text}': expected <instant>,<changed|unchanged>"
                         f"[,<note>]")
    return DeclaredStop(instant=parse_datetime(parts[0]), changed_account=parts[1] == 'changed',
                        note=parts[2] if len(parts) > 2 else '')


def main():
    """Parse the command line and run the command."""
    parser = argparse.ArgumentParser(
        description='The divergence report: a live deployment against backtests of its windows',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    compare_parser = subparsers.add_parser(
        'compare', help='Compare a deployment with backtests of its windows')
    compare_parser.add_argument('--deployment', required=True, help='The deployment id')
    compare_parser.add_argument('--decision-timeframe', required=True,
                                help="The timeframe whose bar's close is a day's decision (D1)")
    compare_parser.add_argument('--reading', choices=[r.value for r in DivergenceReading],
                                default=DivergenceReading.RUN.value,
                                help='The rules the comparison is read by')
    compare_parser.add_argument('--until', default=None,
                                help='The trading-day boundary the comparison ends at (UTC, ISO)')
    compare_parser.add_argument('--records', default=None,
                                help="A copy of the bot host's data home to read the sessions from")
    compare_parser.add_argument('--declared-stop', action='append', default=[],
                                help='A declared stop: <instant>,<changed|unchanged>[,<note>]')
    compare_parser.add_argument('--ledger-credentials',
                                default='venues/kraken_ledger_credentials.json',
                                help="The key that may read the venue's ledger, as configured")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = DivergenceReportCli()

    try:
        if args.command == 'compare':
            sys.exit(cli.cmd_compare(args.deployment, args.decision_timeframe, args.reading,
                                     args.until, args.records, args.declared_stop,
                                     args.ledger_credentials))

    except KeyboardInterrupt:
        print('\n\n⚠️  Interrupted by user')
        sys.exit(130)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
