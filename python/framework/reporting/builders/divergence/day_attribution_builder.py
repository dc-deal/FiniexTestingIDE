"""
The day attribution of the divergence report (#512) — where one compared day's difference of
equity change comes from.

Each run's change of account value over a day is taken apart, from the last valued frame before
the opening to the last one before the closing. With H the quantity held at the start, m the mid,
s the side of a fill (+1 buy, −1 sell), its lots and its price:

    ΔE = H × (m_close − m_open) + Σ s × lots × (m_close − fill) − fees + Δvaluation

and each fill of a paired order splits at its decision mid and its submission mid:

    s × lots × (m_close − fill) = s × lots × [(m_close − m_decision)      end price
                                             − (m_submission − m_decision) delay
                                             − (fill − m_submission)]      execution

A fill of an order the other run did not submit stays whole, as one-sided. The valuation part is
what the account values beside its quote balance and its base holding at the mid — another
currency it holds. The residual is the change less every part — it names a quantity the equation
misses, never noise, and the two runs' parts are then compared part by part. Pure: it reads the
frames and events it is handed.

The equation holds at spot, where a holding is valued at the mid and a fill moves the two balances
by its price (measured 2026-11-09 on a real spot backtest: a residual of 0 to nine places in every
window). At margin a price move is worth the lots times the contract size, converted into the
account currency at the rate of the moment — not linear in the mid, and the frames carry neither
— so a margin day is not taken apart, and says why.
"""

from datetime import date, datetime
from typing import Mapping, Optional, Sequence, Tuple

from python.framework.reporting.builders.divergence.equity_divergence_builder import (
    valued_frame_before,
)
from python.framework.reporting.builders.divergence.order_divergence_builder import (
    FILL_EVENT_TYPES,
    order_side_of,
)
from python.framework.types.api.divergence_report_types import (
    AttributionParts,
    DayAttribution,
    OrderOutcome,
)
from python.framework.types.api.report_types import OrderEventRow
from python.framework.types.api.state_frame_types import StateFrame
from python.framework.types.trading_env_types.order_types import OrderSide
from python.framework.utils.time_utils import parse_iso_datetime

# An order's events by the run that recorded it and its submission's `seq`
OrderKey = Tuple[str, int]

# Why a margin account's day is not taken apart
_MARGIN_NOT_TAKEN_APART = (
    'a margin account: a price move is worth the lots times the contract size, converted into '
    'the account currency at the rate of the moment, and the frames carry neither')


def attribute_day(trading_day: date, opening: datetime, closing: datetime,
                  live_frames: Sequence[StateFrame],
                  live_events: Sequence[Tuple[str, OrderEventRow]],
                  live_paired: Mapping[OrderKey, OrderOutcome],
                  backtest_frames: Sequence[StateFrame],
                  backtest_events: Sequence[Tuple[str, OrderEventRow]],
                  backtest_paired: Mapping[OrderKey, OrderOutcome],
                  base_currency: Optional[str]) -> DayAttribution:
    """
    One compared day's difference of equity change, taken apart.

    Args:
        trading_day: The day's label
        opening: When it opened
        closing: When it closed
        live_frames: The live sessions' frames, in time order
        live_events: The live order events, each with the session that recorded it
        live_paired: The live orders the backtest also submitted that day, by session and `seq`
        backtest_frames: The backtest scenario's frames, in time order
        backtest_events: Its order events, each with the backtest's run id
        backtest_paired: Its orders the session also submitted that day, by run and `seq`
        base_currency: The symbol's base currency — the holding a spot account values

    Returns:
        Both runs' days taken apart and their difference; where a run's day cannot be, why
    """
    live, live_why = decompose_day(opening, closing, live_frames, live_events, live_paired,
                                   base_currency)
    backtest, backtest_why = decompose_day(opening, closing, backtest_frames, backtest_events,
                                           backtest_paired, base_currency)
    if live is None or backtest is None:
        why = '; '.join(f'{side}: {reason}' for side, reason in
                        (('live', live_why), ('backtest', backtest_why)) if reason)
        return DayAttribution(trading_day=trading_day, closing=closing, live=live,
                              backtest=backtest, not_computed_because=why)
    return DayAttribution(trading_day=trading_day, closing=closing, live=live, backtest=backtest,
                          difference=_difference(live, backtest))


def decompose_day(opening: datetime, closing: datetime, frames: Sequence[StateFrame],
                  events: Sequence[Tuple[str, OrderEventRow]],
                  paired: Mapping[OrderKey, OrderOutcome],
                  base_currency: Optional[str]) -> Tuple[Optional[AttributionParts], str]:
    """
    One run's change of account value over a day, taken apart.

    Args:
        opening: When the day opened
        closing: When it closed
        frames: The run's frames, in time order
        events: The run's order events, each with the run unit that recorded it
        paired: The run's orders the other run also submitted that day
        base_currency: The symbol's base currency

    Returns:
        (the parts, '') — or (None, why the day cannot be taken apart)
    """
    start = valued_frame_before(frames, opening)
    end = valued_frame_before(frames, closing)
    if start is None or end is None or end.frame_time <= start.frame_time:
        return None, 'no valued frame before the opening and a later one before the closing'
    if start.last_price is None or end.last_price is None:
        return None, "a frame at the day's ends carries no mid"
    if start.balances is None or end.balances is None:
        return None, _MARGIN_NOT_TAKEN_APART
    if base_currency is None:
        return None, "the symbol's base currency is not known"
    held = start.balances.get(base_currency, 0.0) * (end.last_price - start.last_price)
    delay = execution = end_price = one_sided = fees = 0.0
    for run_id, event in events:
        fill_time = _fill_time(event)
        if fill_time is None or not start.frame_time < fill_time <= end.frame_time:
            continue
        sign = _sign(event)
        if sign is None:
            return None, f'the fill {run_id} seq {event.seq} names no action or direction'
        lots, price = event.lots, event.fill_price
        fees -= event.fee or 0.0
        outcome = paired.get((run_id, event.submitted_seq)) if event.submitted_seq else None
        if outcome is None:
            one_sided += sign * lots * (end.last_price - price)
            continue
        decision = outcome.decision_mid or outcome.submission_mid or price
        submission = outcome.submission_mid or decision
        end_price += sign * lots * (end.last_price - decision)
        delay -= sign * lots * (submission - decision)
        execution -= sign * lots * (price - submission)
    valuation = _valuation(end, base_currency) - _valuation(start, base_currency)
    equity_change = end.equity - start.equity
    residual = equity_change - (held + delay + execution + end_price + one_sided + fees
                                + valuation)
    return AttributionParts(
        equity_change=equity_change, held=held, delay=delay, execution=execution,
        end_price=end_price, one_sided=one_sided, fees=fees, valuation=valuation,
        residual=residual), ''


def _valuation(frame: StateFrame, base_currency: str) -> float:
    """
    What a spot frame's account value holds beside its quote balance and its base holding at
    the mid — another currency the account holds.

    Args:
        frame: A valued spot frame with a mid
        base_currency: The symbol's base currency

    Returns:
        The account value less the quote balance and the base holding at the mid
    """
    balances = frame.balances or {}
    return frame.equity - (balances.get(frame.account_currency, 0.0)
                           + balances.get(base_currency, 0.0) * frame.last_price)


def _fill_time(event: OrderEventRow) -> Optional[datetime]:
    """
    When an event executed lots, where it is a priced fill.

    Args:
        event: An order event

    Returns:
        Its time; None where it is no fill, executed nothing or carries no price or time
    """
    if (event.event_type not in FILL_EVENT_TYPES or not event.lots
            or event.fill_price is None or not event.event_time):
        return None
    return parse_iso_datetime(event.event_time)


def _sign(event: OrderEventRow) -> Optional[float]:
    """
    A fill's side as a sign.

    Args:
        event: The fill

    Returns:
        +1 for a buy, −1 for a sell; None where the event names no action or direction
    """
    side = order_side_of(event)
    if side is None:
        return None
    return 1.0 if side is OrderSide.BUY else -1.0


def _difference(live: AttributionParts, backtest: AttributionParts) -> AttributionParts:
    """
    Two runs' days, part by part, live minus backtest.

    Args:
        live: The live day
        backtest: The backtest's

    Returns:
        The differences — their residual is the attribution's
    """
    return AttributionParts(**{name: getattr(live, name) - getattr(backtest, name)
                               for name in AttributionParts.model_fields})
