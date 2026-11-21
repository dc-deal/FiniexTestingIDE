"""
FiniexTestingIDE - Trade Completeness Summary

What the comparison of our files with the venue's own trade list found over one window (#647).
Formatting only — the comparison is derived by the completeness check.
"""

from python.framework.types.coverage_report_types import TradeCompletenessResult

# How many runs of missing trades are listed, longest first
_RUNS_SHOWN = 10


def print_trade_completeness(result: TradeCompletenessResult) -> None:
    """
    Print the comparison of our trades with the venue's over one window.

    Args:
        result: The comparison
    """
    print(f"\n{'='*60}")
    print(f'🔎 TRADE COMPLETENESS: {result.broker_type}/{result.symbol}')
    print(f"{'='*60}")
    print(f'Window:        {result.start:%Y-%m-%d %H:%M:%S} → {result.end:%Y-%m-%d %H:%M:%S} UTC')
    print(f'Our trades:    {result.our_trades:,}')
    if result.not_measurable_because:
        print(f'Not measurable: {result.not_measurable_because}')
        print('               nothing is compared; choose a window from collector format 1.7.0 on')
        print(f"{'='*60}\n")
        return
    if result.venue_trades is None:
        print(f'Venue trades:  not read — {result.not_read_because}')
        print('               nothing is compared; the window is neither complete nor incomplete')
        print(f"{'='*60}\n")
        return
    share = f'{result.missing_trades / result.venue_trades * 100:.3f} %' if result.venue_trades else 'n/a'
    print(f'Venue trades:  {result.venue_trades:,}')
    print(f'Missing:       {result.missing_trades:,} ({share}) in {len(result.runs):,} run(s)')
    if result.extra_trades:
        print(f'⚠️  Not in the venue\'s list: {result.extra_trades:,} trade(s) our files hold')
    for run in result.runs[:_RUNS_SHOWN]:
        seconds = (run.last_at - run.first_at).total_seconds()
        print(f'   {run.first_at:%Y-%m-%d %H:%M:%S} UTC   {run.trades:>6,} trade(s) over {seconds:,.0f} s')
    if len(result.runs) > _RUNS_SHOWN:
        print(f'   ... and {len(result.runs) - _RUNS_SHOWN:,} shorter run(s)')
    print(f"{'='*60}\n")
