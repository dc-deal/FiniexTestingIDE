"""
FiniexTestingIDE - Booking Periods (#537)

The run's Hauptbuch, one line per booking period, and a total line that RECONCILES.

Formatting only — every figure was derived in `booking_periods_report_builder` (§12). The last
line is what the table is for: the sum of the periods against the figure the run reports by its
own independent path. Two derivations of one number meeting is evidence; one number printed
twice is not.

**The summary FILE always gets the whole table; only the CONSOLE is ever trimmed.** That is the
project's rule for every end-of-run section, and it is what `summary.detail` is for: a file may
grow, a terminal may not. The compact console form therefore drops the ROWS and keeps the
reconciliation, because the reconciliation is a CHECK rather than a detail — it is the line that
says the ledger rows this run just wrote are complete.
"""

from python.framework.types.api.report_types import BookingPeriodsReport
from python.framework.utils.console_renderer import ConsoleRenderer

# The period's close reason, as one character in a column rather than a word in every row.
_REASON_MARK = {
    'anchor': ' ',            # the ordinary case: the market's own day boundary
    'session_end': '●',       # the last, necessarily incomplete period
    'operator': '◆',          # a person asked for it
}


class BookingPeriodsSummary:
    """The run's Hauptbuch as an ordered console section, fed by `RunConsoleRenderer`."""

    def __init__(self, report: BookingPeriodsReport):
        """
        Args:
            report: The derived booking-periods report
        """
        self._report = report

    def render(self, renderer: ConsoleRenderer, summary_detail: bool, threshold: int) -> None:
        """
        Print the section, in full or compacted to its check.

        Args:
            renderer: The console renderer, for signature parity with the other sections —
                this table draws its own columns and needs no colour
            summary_detail: False → the console compact form (heading + reconciliation)
            threshold: Units at or below which every period is printed
        """
        render_booking_periods(self._report, detail_threshold=threshold,
                               compact=not summary_detail)


def render_booking_periods(
    report: BookingPeriodsReport,
    indent: str = '  ',
    detail_threshold: int = 1,
    compact: bool = False,
) -> None:
    """
    Print the booking periods as a table.

    Silent when the run booked none — an empty heading would read as "this run traded nothing".

    **A run with many units collapses rather than scrolling.** A robustness set books one period
    per trading day per SCENARIO, and forty scenarios over three-day windows is a hundred and
    twenty lines — the reader loses the run summary above it and gains nothing. Above the
    threshold each unit is one line instead, and the line says how many periods it stands for,
    so nothing is dropped silently. The threshold is the console's existing
    `scenario_detail_threshold`, deliberately: this is the same question the per-scenario detail
    already answers, and a second rule would be a second answer.

    Args:
        report: The derived report
        indent: Left padding, so the block nests under a caller's heading
        detail_threshold: Units at or below which every period is printed
        compact: True → the heading and the reconciliation alone, no rows. The console form of
            a run whose `summary.detail` is off; the summary FILE is never rendered this way
    """
    if not report.periods:
        return

    units = _by_unit(report)
    if compact:
        _render_compact(report, units, indent)
        return
    if len(units) > detail_threshold:
        _render_collapsed(report, units, indent)
        return

    print(f'\n{indent}{_heading(report, units)}')
    print(f'{indent}' + '─' * 104)
    print(f'{indent}{"#":>3}  {"opened":<17} {"closed":<17} {"trades":>6} {"net P&L":>11} '
          f'{"fees":>8} {"win":>6} {"equity":>12} {"period DD":>11}')
    print(f'{indent}' + '─' * 104)

    # The unit is a SUB-HEADING rather than a column: with one unit — the live session, and the
    # comparison backtest the parity proof actually uses — it would be the same string on every
    # line, and the table is already as wide as a terminal allows.
    for unit_name, rows in units.items():
        if len(units) > 1:
            print(f'{indent}  ▸ {unit_name}')
        _render_period_rows(rows, indent)

    print(f'{indent}' + '─' * 104)
    print(f'{indent}{"Σ":>3}  {"":<17} {"":<17} {report.total_trades:>6} '
          f'{report.total_net_pnl:>11.2f} {report.total_fees:>8.2f} {"":>6} '
          f'{report.final_equity:>12.2f} {-abs(report.deepest_period_drawdown):>11.2f}')

    _render_reconciliation(report, indent)
    print(f'{indent}● = the last period, closed by the session ending rather than by the '
          f'market · ◆ = closed by hand')
    print(f'{indent}A trade is booked in the period it was CLOSED in, so a position held '
          f'overnight pays out')
    print(f'{indent}on the later day. The equity column is what the account DID; the P&L '
          f'column is what was BOOKED.')


def _render_period_rows(rows, indent: str) -> None:
    """
    One line per booking period.

    Args:
        rows: The periods of one unit
        indent: Left padding
    """
    for row in rows:
        mark = _REASON_MARK.get(row.reason, '?')
        print(f'{indent}{row.segment_no:>3}{mark} {_stamp(row.opened_at):<17} '
              f'{_stamp(row.closed_at):<17} {row.trade_count:>6} {row.net_pnl:>11.2f} '
              f'{row.total_fees:>8.2f} {row.win_rate * 100:>5.0f}% {row.final_equity:>12.2f} '
              f'{-abs(row.max_drawdown):>11.2f}')


def _render_reconciliation(report: BookingPeriodsReport, indent: str) -> None:
    """
    State whether the periods add up to the run, and say so either way.

    The agreeing case prints too. An absent line cannot be told apart from a check that never
    ran, and this is the line that makes the column above trustworthy.

    Args:
        report: The derived report
        indent: Left padding
    """
    if report.reconciles:
        print(f'{indent}    ✓ reconciles with the run total '
              f'({report.run_net_pnl:.2f} {report.currency}, '
              f'{report.run_total_trades} trade(s)) — the periods account for everything')
        return

    difference = report.total_net_pnl - report.run_net_pnl
    print(f'{indent}    ⚠️  DOES NOT RECONCILE — the periods sum to '
          f'{report.total_net_pnl:.2f} while the run reports {report.run_net_pnl:.2f} '
          f'({difference:+.2f} {report.currency})')
    print(f'{indent}       The two figures come from different derivations over the same '
          f'trades, so they')
    print(f'{indent}       cannot both be right. A trade realised outside every period, or '
          f'one counted twice,')
    print(f'{indent}       is what this difference looks like — start at the period whose '
          f'trade count surprises you.')


def _stamp(iso: str) -> str:
    """
    An ISO instant as the table shows it — date and minute, no timezone.

    Every instant in this table is UTC and on one axis, so repeating the offset on every line
    spends a column on a constant.

    Args:
        iso: The stored instant

    Returns:
        `MM-DD HH:MM`, or the raw value when it cannot be read
    """
    try:
        date, rest = iso.split('T')
        return f'{date[5:]} {rest[:5]}'
    except (ValueError, IndexError):
        return iso


def _by_unit(report: BookingPeriodsReport) -> dict:
    """
    The report's periods grouped by their run unit, in first-appearance order.

    Args:
        report: The derived report

    Returns:
        Unit name → its periods
    """
    grouped: dict = {}
    for row in report.periods:
        grouped.setdefault(row.unit_name, []).append(row)
    return grouped


def _heading(report: BookingPeriodsReport, units: dict) -> str:
    """
    The section's title line, naming the unit count only when there is more than one.

    Args:
        report: The derived report
        units: Unit name -> its periods

    Returns:
        The heading text
    """
    over = f' over {len(units)} unit(s)' if len(units) > 1 else ''
    return (f'📕 BOOKING PERIODS — {len(report.periods)} period(s){over} · '
            f'{report.currency}')


def _render_compact(report: BookingPeriodsReport, units: dict, indent: str) -> None:
    """
    The heading and the reconciliation, nothing else — the console form when detail is off.

    The rows go, the CHECK stays. Dropping the reconciliation as well would leave a compact run
    with no statement that its ledger rows are complete, and a compact run is precisely the one
    nobody re-reads. Nothing is lost by it: the summary file is rendered with detail ON in both
    pipelines, so the full table is always on disk.

    Args:
        report: The derived report
        units: Unit name -> its periods
        indent: Left padding
    """
    print(f'\n{indent}{_heading(report, units)}')
    _render_reconciliation(report, indent)


def _render_collapsed(report: BookingPeriodsReport, units: dict, indent: str) -> None:
    """
    One line per unit instead of one per period, for a run with many units.

    Everything the full table would show is still in the artifact and in the ledger; what is
    collapsed is only the console. The period COUNT is on every line so the reader can see what
    each line stands for — a collapse that does not say what it collapsed is a silent cap.

    Args:
        report: The derived report
        units: Unit name → its periods
        indent: Left padding
    """
    print(f'\n{indent}{_heading(report, units)}')
    print(f'{indent}' + '─' * 104)
    print(f'{indent}{"unit":<40} {"periods":>8} {"trades":>7} {"net P&L":>13} '
          f'{"deepest period DD":>19}')
    print(f'{indent}' + '─' * 104)
    for name, rows in units.items():
        deepest = min((row.max_drawdown for row in rows), default=0.0)
        print(f'{indent}{name[:40]:<40} {len(rows):>8} '
              f'{sum(row.trade_count for row in rows):>7} '
              f'{sum(row.net_pnl for row in rows):>13.2f} {-abs(deepest):>19.2f}')
    print(f'{indent}' + '─' * 104)
    print(f'{indent}{"Σ":<40} {len(report.periods):>8} {report.total_trades:>7} '
          f'{report.total_net_pnl:>13.2f} '
          f'{-abs(report.deepest_period_drawdown):>19.2f}')
    _render_reconciliation(report, indent)
    print(f'{indent}Collapsed because the run has more units than the console detail '
          f'threshold. Every period is')
    print(f'{indent}in the ledger and in the run artifact — this view trades depth for a '
          f'table that fits.')
