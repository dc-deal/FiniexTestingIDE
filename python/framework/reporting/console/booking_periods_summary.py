"""
FiniexTestingIDE - Booking Periods (#537)

The run's Hauptbuch, one line per booking period, and a total line that RECONCILES.

Formatting only — every figure was derived in `booking_periods_report_builder` (§12). The last
line is what the table is for: the sum of the periods against the figure the run reports by its
own independent path. Two derivations of one number meeting is evidence; one number printed
twice is not.
"""

from python.framework.types.api.report_types import BookingPeriodsReport

# The period's close reason, as one character in a column rather than a word in every row.
_REASON_MARK = {
    'anchor': ' ',            # the ordinary case: the market's own day boundary
    'session_end': '●',       # the last, necessarily incomplete period
    'operator': '◆',          # a person asked for it
}


def render_booking_periods(report: BookingPeriodsReport, indent: str = '  ') -> None:
    """
    Print the booking periods as a table.

    Silent when the run booked none — which is every simulation until it books too, and every
    run written before this feature. An empty heading would read as "this run traded nothing".

    Args:
        report: The derived report
        indent: Left padding, so the block nests under a caller's heading
    """
    if not report.periods:
        return

    print(f'\n{indent}📕 BOOKING PERIODS — {len(report.periods)} period(s) · '
          f'{report.currency}')
    print(f'{indent}' + '─' * 104)
    print(f'{indent}{"#":>3}  {"opened":<17} {"closed":<17} {"trades":>6} {"net P&L":>11} '
          f'{"fees":>8} {"win":>6} {"equity":>12} {"period DD":>11}')
    print(f'{indent}' + '─' * 104)

    for row in report.periods:
        mark = _REASON_MARK.get(row.reason, '?')
        print(f'{indent}{row.segment_no:>3}{mark} {_stamp(row.opened_at):<17} '
              f'{_stamp(row.closed_at):<17} {row.trade_count:>6} {row.net_pnl:>11.2f} '
              f'{row.total_fees:>8.2f} {row.win_rate * 100:>5.0f}% {row.final_equity:>12.2f} '
              f'{-abs(row.max_drawdown):>11.2f}')

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
