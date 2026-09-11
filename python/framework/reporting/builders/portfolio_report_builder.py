"""
Portfolio report builder (#391) — the headline-P&L + full-projection postprocessor.

The unified array model: a run is a list of units (sim: N scenarios; live: 1 session) plus a
per-currency roll-up. Consumes the run's `RunUnit` list and maps each unit's `PortfolioStats`
to the full per-unit projection (the per-scenario linear console block renders purely from it).
The per-currency roll-up is derived from the rows via the shared `aggregate_portfolio_by_currency`
(one builder for both pipelines; mirrors the console `PortfolioAggregator` formulas).
"""

from typing import List

from python.framework.reporting.builders.report_aggregators import aggregate_portfolio_by_currency
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.types.api.report_types import (
    OpenPositionRow,
    PortfolioReport,
    PortfolioUnitRow,
)
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import ProtectiveLevelEnforcement


def build_portfolio_report(run_id: str, units: List[RunUnit]) -> PortfolioReport:
    """
    Build the portfolio report from the run's units (per-unit rows + per-currency roll-up).

    Args:
        run_id: The run this report belongs to
        units: The run's units (sim: N scenarios; live: 1 session)

    Returns:
        PortfolioReport with one full-projection row per unit (with stats) + per-currency aggregate
    """
    rows = [_to_unit_row(u) for u in units if u.portfolio_stats is not None]
    return PortfolioReport(run_id=run_id, units=rows, aggregates=aggregate_portfolio_by_currency(rows))


def _spot_estimate(stats) -> tuple:
    """Estimated spot portfolio value from the dual balance (quote + base x last price).

    Args:
        stats: The unit's portfolio stats (carries balances + the stamped currency split)

    Returns:
        (current, initial, pnl, pnl_pct) — all zero when the unit is not spot or has no price
    """
    if not stats.spot_mode or stats.last_price <= 0:
        return 0.0, 0.0, 0.0, 0.0
    quote, base = stats.quote_currency, stats.base_currency
    current = stats.balances.get(quote, 0.0) + stats.balances.get(base, 0.0) * stats.last_price
    initial = (stats.initial_balances.get(quote, 0.0)
               + stats.initial_balances.get(base, 0.0) * stats.last_price)
    pnl = current - initial
    return current, initial, pnl, (pnl / initial * 100) if initial > 0 else 0.0


def _open_position_rows(
    positions: List[Position], last_price: float,
    enforcement: str = '') -> List[OpenPositionRow]:
    """Project the positions a unit still held onto their report rows (#492).

    Args:
        positions: The unit's open positions at run end
        last_price: The unit's last mid price — 0.0 when no tick ever arrived
        enforcement: Who enforces a protective level by DEFAULT in the executor that
            produced these positions, stamped at capture — carried only onto rows that
            HAVE a level (#500). A position whose protective order the venue confirmed
            overrides it for itself (#503), so a mixed run reports each row truthfully

    Returns:
        One row per position; `valued` is False where there was no price to mark against
    """
    return [
        OpenPositionRow(
            position_id=position.position_id,
            direction=position.direction.value,
            lots=position.lots,
            entry_price=position.entry_price,
            entry_time=position.entry_time.isoformat(),
            last_price=last_price,
            unrealized_pnl=position.unrealized_pnl,
            valued=last_price > 0,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            # Only where there is something to enforce — an empty string on a position
            # without levels reads correctly as "nothing to hold".
            protective_level_enforcement=(
                _enforcement_of(position, enforcement)
                if position.stop_loss is not None or position.take_profit is not None
                else ''),
            take_profit_enforcement=(
                _take_profit_enforcement_of(position, enforcement)
                if position.stop_loss is not None or position.take_profit is not None
                else ''),
        )
        for position in positions
    ]


def _enforcement_of(position: Position, run_default: str) -> str:
    """Who holds THIS position's protective level, for its report row (#503).

    The run-wide answer stamped at capture is only the DEFAULT. A position whose
    protective order the venue confirmed answers for itself, so a mixed run reports each
    row truthfully instead of painting one label across all of them. The derivation is
    the position's own, shared with the tick check that acts on it — the report and the
    enforcer can never disagree.

    Args:
        position: The open position
        run_default: The executor's run-wide answer as captured, '' when none was

    Returns:
        The enforcement value for the row, or '' when the run captured no answer
    """
    if not run_default:
        return ''
    return position.protective_enforcement(
        ProtectiveLevelEnforcement(run_default)).value


def _take_profit_enforcement_of(position, run_default: str) -> str:
    """Who enforces the TARGET, which is not always who enforces the stop (#503).

    Only one order can rest at the venue — Kraken offers neither OCO nor a bracket — and the
    one placed holds the STOP. So a position the venue protects still has a take profit that
    only this process watches, and saying otherwise tells an operator a level survives a
    restart when it does not.

    Args:
        position: The open position
        run_default: The executor's run-wide answer as captured, '' when none was

    Returns:
        The target's enforcement value, or '' when the run captured no answer
    """
    stop_answer = _enforcement_of(position, run_default)
    if stop_answer == ProtectiveLevelEnforcement.VENUE.value:
        return ProtectiveLevelEnforcement.LOCAL.value
    return stop_answer


def _final_equity(stats, unrealized_pnl: float, est_current: float) -> float:
    """The wealth view: what the unit was worth when it ended.

    Spot holds its value in the BALANCES, so the dual-balance estimate already is the
    portfolio value; margin holds it in the position, so it is balance plus the mark. Two
    formulas because the two account models put the same money in different places — the
    same reason `get_account_info` overrides its own equity for spot.

    Args:
        stats: The unit's portfolio stats
        unrealized_pnl: The mark on the unit's open positions
        est_current: The spot dual-balance estimate (0.0 when not spot or unpriced)

    Returns:
        The unit's final equity, or its balance when nothing could be valued
    """
    if stats.spot_mode:
        return est_current if est_current > 0 else stats.current_balance
    return stats.current_balance + unrealized_pnl


def _usable_funds(stats: PortfolioStats) -> dict:
    """
    What is left of each balance once this bot's own unfilled orders are counted (#489).

    Derived here rather than in a renderer: the same difference is read by the console, the
    JSON artifact and the API, and three subtractions are three chances to disagree. Only
    currencies with a claim appear — a balance nothing is holding needs no second number.

    Args:
        stats: The unit's portfolio stats, carrying the captured committed figure

    Returns:
        currency → balance minus claim; empty when nothing is committed
    """
    return {
        currency: stats.balances.get(currency, 0.0) - claimed
        for currency, claimed in stats.committed_funds.items()
        if claimed
    }


def _to_unit_row(unit: RunUnit) -> PortfolioUnitRow:
    """Map a unit's portfolio stats to the full per-unit projection row."""
    stats = unit.portfolio_stats
    est_current, est_initial, est_pnl, est_pnl_pct = _spot_estimate(stats)
    open_rows = _open_position_rows(
        unit.open_positions, stats.last_price, stats.protective_level_enforcement)
    return PortfolioUnitRow(
        name=unit.name,
        symbol=unit.symbol,
        currency=stats.currency,
        total_trades=stats.total_trades,
        winning_trades=stats.winning_trades,
        losing_trades=stats.losing_trades,
        win_rate=stats.win_rate,
        profit_factor=stats.profit_factor,
        total_profit=stats.total_profit,
        total_loss=stats.total_loss,
        net_profit=stats.total_profit - stats.total_loss,
        max_drawdown=stats.max_drawdown,
        max_dd_pct=(stats.max_drawdown / stats.max_equity * 100) if stats.max_equity > 0 else 0.0,
        total_fees=stats.total_fees,
        data_source=unit.data_source,
        sentiment_source=unit.sentiment_source,
        broker_name=stats.broker_name,
        spot_mode=stats.spot_mode,
        total_long_trades=stats.total_long_trades,
        total_short_trades=stats.total_short_trades,
        max_equity=stats.max_equity,
        current_balance=stats.current_balance,
        initial_balance=stats.initial_balance,
        conversion_rate=stats.current_conversion_rate,
        total_spread_cost=stats.total_spread_cost,
        total_commission=stats.total_commission,
        total_swap=stats.total_swap,
        maker_fee=stats.maker_fee,
        taker_fee=stats.taker_fee,
        has_error=unit.has_error,
        balances=stats.balances,
        initial_balances=stats.initial_balances,
        committed_funds=dict(stats.committed_funds),
        usable_funds=_usable_funds(stats),
        last_price=stats.last_price,
        base_currency=stats.base_currency,
        quote_currency=stats.quote_currency,
        spot_est_current=est_current,
        spot_est_initial=est_initial,
        spot_est_pnl=est_pnl,
        spot_est_pnl_pct=est_pnl_pct,
        # #492 — what the unit still held, and what it was worth. `net_profit` above stays
        # realised; these never merge into it.
        open_positions=open_rows,
        unrealized_pnl=stats.unrealized_pnl,
        final_equity=_final_equity(stats, stats.unrealized_pnl, est_current),
        # A mark-to-market only when everything still open could actually be marked.
        final_equity_valued=all(row.valued for row in open_rows),
        session_end_policy=unit.session_end_policy,
    )
