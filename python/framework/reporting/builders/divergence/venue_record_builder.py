"""
What the venue's own record says about a comparison in the divergence report (#512).

Three questions, each answered from what the venue published or booked, never from our own
records alone: how many trades the venue published inside a run of ids our archive lacks (the
venue also allocates ids it never publishes, so a run of missing ids alone proves nothing); how
many of the venue's trades in a decision bar our archive lacks, for a day proposed as a capture
difference; and which movements of the account in the venue's ledger are no order of the bot —
each of them ends the compared window, by the verdict rules' section on the window.

Pure: the venue's trades and ledger entries are handed in by the venue side's collector.
"""

from datetime import datetime, timedelta
from typing import Collection, Iterable, List, Optional, Sequence, Set, Tuple

from python.framework.types.api.divergence_report_types import (
    DecisionComparison,
    DecisionComparisonState,
    DivergenceGap,
    ExplainedFlipKind,
    VenueAccountChange,
)
from python.framework.types.api.report_types import OrderEventRow
from python.framework.types.venue_history_types import (
    VenueLedgerEntry,
    VenueLedgerEntryKind,
    VenueTrade,
)
from python.framework.utils.timeframe_config_utils import TimeframeConfig


def trades_inside_run(run: DivergenceGap, trades: Sequence[VenueTrade]) -> int:
    """
    How many trades the venue published between the two archive trades around a run of missing
    ids — strictly between them, so neither edge counts.

    Args:
        run: The run, with its two archive trade ids
        trades: The venue's trades from the edge before it to the edge after it

    Returns:
        The count; 0 where the venue allocated the ids without publishing a trade
    """
    if run.trade_id_before is None or run.trade_id_after is None:
        raise ValueError(f'The run {run.start.isoformat()} → {run.end.isoformat()} names no '
                         f'archive trade ids around it.')
    return sum(1 for trade in trades
               if run.trade_id_before < trade.trade_id < run.trade_id_after)


def decision_bar_of(comparison: DecisionComparison,
                    decision_timeframe: str) -> Optional[Tuple[datetime, datetime]]:
    """
    The decision bar of a compared day — the bar whose close the decision followed.

    Args:
        comparison: The day's comparison
        decision_timeframe: The timeframe whose bar's close is the decision

    Returns:
        (its opening, its close); None where neither side's frame names the bar — a segment's
        first day is decided at its first tick
    """
    opening = next((decision.decision_bar_open for decision in (comparison.live,
                                                                comparison.backtest)
                    if decision is not None and decision.decision_bar_open is not None), None)
    if opening is None:
        return None
    return opening, opening + timedelta(minutes=TimeframeConfig.get_minutes(decision_timeframe))


def capture_bars(comparisons: Iterable[DecisionComparison],
                 decision_timeframe: str) -> List[Tuple[datetime, datetime]]:
    """
    The decision bars whose trades confirm or refute a capture difference — one per one-sided day
    proposed as one.

    Args:
        comparisons: The compared days
        decision_timeframe: The timeframe whose bar's close is the decision

    Returns:
        (opening, close) per such day that names its bar, in day order
    """
    bars = [decision_bar_of(comparison, decision_timeframe) for comparison in comparisons
            if comparison.state is DecisionComparisonState.ONE_SIDED
            and comparison.proposed_kind is ExplainedFlipKind.CAPTURE]
    return [bar for bar in bars if bar is not None]


def confirm_capture(comparison: DecisionComparison, archive_ids: Collection[int],
                    trades: Sequence[VenueTrade]) -> DecisionComparison:
    """
    A day proposed as a capture difference, with what the venue's trades in its decision bar say.

    Args:
        comparison: The day's comparison, proposed as a capture difference
        archive_ids: The trade ids our archive holds in the decision bar
        trades: The venue's trades in the decision bar

    Returns:
        The comparison, confirmed where our archive lacks trades the venue published in the bar —
        the backtest then rendered the bar from fewer trades than there were. Refuted where the
        archive holds every one: the difference is then not our capture
    """
    missing = [trade for trade in trades if trade.trade_id not in archive_ids]
    found = (f"confirmed: the archive lacks {len(missing)} of the venue's {len(trades)} trades in "
             f"the decision bar" if missing
             else f"not confirmed: the archive holds every one of the venue's {len(trades)} trades "
                  f"in the decision bar")
    detail = comparison.detail.replace(" · unconfirmed until the venue's trades show it", '')
    return comparison.model_copy(update={
        'capture_confirmed': bool(missing), 'capture_missing_trades': len(missing),
        'detail': f'{detail} · {found}'})


def bot_order_refs(order_events: Iterable[OrderEventRow]) -> Set[str]:
    """
    The venue's handles of every order the bot sent, as its order events carry them.

    Args:
        order_events: The sessions' order events

    Returns:
        Every handle, the ones an amend replaced included
    """
    refs: Set[str] = set()
    for event in order_events:
        refs.update(ref for ref in (event.broker_ref, event.previous_broker_ref) if ref)
    return refs


def account_changes_of(entries: Sequence[VenueLedgerEntry], bot_orders: Collection[str],
                       assets: Collection[str]) -> List[VenueAccountChange]:
    """
    The movements of the account in the venue's ledger that are no order of the bot.

    Args:
        entries: The ledger's entries, in booking order
        bot_orders: The venue's handles of the bot's orders
        assets: The currencies the compared account holds — its base and its quote; an entry in
            any other currency moves nothing the comparison values

    Returns:
        One change per such entry, in booking order: a deposit, a withdrawal, a conversion, a
        transfer, a reward, an adjustment, or a trade of an order the bot did not send
    """
    changes: List[VenueAccountChange] = []
    for entry in entries:
        if entry.asset not in assets or entry.get_balance_change() == 0:
            continue
        if entry.kind.may_belong_to_an_order() and entry.order_ref in bot_orders:
            continue
        changes.append(VenueAccountChange(
            at=entry.executed_at, kind=entry.kind, asset=entry.asset,
            balance_change=entry.get_balance_change(), venue_ref=entry.venue_ref,
            order_ref=entry.order_ref, detail=_why(entry)))
    return changes


def _why(entry: VenueLedgerEntry) -> str:
    """
    Why a ledger entry is a change of the account, in a few words.

    Args:
        entry: The entry

    Returns:
        The words, with what it moved
    """
    moved = f'{entry.get_balance_change():+.8g} {entry.asset}'
    if entry.kind is VenueLedgerEntryKind.TRADE:
        if entry.order_ref is None:
            return f'a trade the venue named no order for ({moved})'
        return (f'a trade of order {entry.order_ref}, which no order event of the bot names '
                f'({moved})')
    return f'{entry.kind.value} ({entry.venue_type}) of {moved}'
