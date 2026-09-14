"""
Safety report builder (#356 / #314) — the risk-denominator postprocessor.

Maps what the tick loop measured (`SafetySessionRecord`) plus what the profile armed
(`SafetyConfig`) onto the `SafetyReport` model. Live-only: a simulation has no circuit
breaker, and no baseline to carry across a restart.

Two things are COMPUTED here and nowhere else. The share of each threshold the worst
excursion consumed — the one figure that answers "how close did it come" without the reader
dividing two numbers themselves — and which config key the account model's floor was written
under. Both are on the model afterwards, so the console, the API and a CSV all read the same
number instead of three renderers re-deriving it (#391).

Nothing is invented. Where a threshold is not configured the usage figure is None rather than
zero: "no limit" and "nothing used of the limit" are different statements, and a reader
cannot tell them apart once both are 0.0.
"""

from typing import Optional

from python.framework.types.api.report_types import (
    SafetyDayRow,
    SafetyLimits,
    SafetyReport,
)
from python.framework.types.autotrader_types.autotrader_config_types import SafetyConfig
from python.framework.types.autotrader_types.safety_session_types import SafetySessionRecord
from python.framework.types.persistence_types import BaselineOrigin


def build_safety_report_from_session(
    run_id: str,
    record: SafetySessionRecord,
    safety: SafetyConfig,
    symbol: str,
) -> SafetyReport:
    """
    Build the safety report for one live session.

    Args:
        run_id: The run this report belongs to
        record: What the tick loop measured against the session's denominators
        safety: The profile's circuit-breaker configuration
        symbol: The instrument this session traded

    Returns:
        The report, ready to persist and to render
    """
    baseline = record.baseline
    spot_mode = record.spot_mode
    baseline_value = baseline.value if baseline is not None else 0.0
    # The deepest day, picked ONCE. A thirty-day run cannot put thirty rows on a console, so
    # something has to choose — and if a renderer chooses, the console and the API answer the
    # same question differently. It names the DAY rather than only the number, because a loss
    # of 310 means nothing without the baseline it was measured against, which sits in its row.
    worst_day = max(record.days, key=lambda day: day.worst_loss_abs, default=None)

    return SafetyReport(
        run_id=run_id,
        symbol=symbol,
        enabled=record.enabled,
        baseline=baseline,
        baseline_value=baseline_value,
        baseline_restored=(
            baseline is not None
            and baseline.origin is BaselineOrigin.RESTORED_CARRY_OVER),
        final_value=record.final_value,
        worst_drawdown_abs=record.worst_drawdown_abs,
        worst_drawdown_abs_at=record.worst_drawdown_abs_at,
        worst_drawdown_pct=record.worst_drawdown_pct,
        worst_drawdown_pct_at=record.worst_drawdown_pct_at,
        soft_limit_used_pct=_limit_used(
            record.worst_drawdown_pct, safety.max_drawdown_pct,
            record.worst_drawdown_abs, safety.max_drawdown_abs),
        hard_limit_used_pct=_limit_used(
            record.worst_drawdown_pct, safety.max_drawdown_pct_hard,
            record.worst_drawdown_abs, safety.max_drawdown_abs_hard),
        block_count=record.block_count,
        blocked_at_end=record.blocked_at_end,
        reason_at_end=record.reason_at_end,
        limits=SafetyLimits(
            min_floor=safety.min_equity if spot_mode else safety.min_balance,
            min_floor_key='min_equity' if spot_mode else 'min_balance',
            max_drawdown_pct=safety.max_drawdown_pct,
            max_drawdown_abs=safety.max_drawdown_abs,
            max_daily_loss_abs=safety.max_daily_loss_abs,
            max_daily_loss_pct=safety.max_daily_loss_pct,
            baseline_mode=safety.baseline_mode,
            persist_baseline=safety.persist_baseline,
            emergency_flatten_enabled=safety.emergency_flatten_enabled,
            max_drawdown_pct_hard=safety.max_drawdown_pct_hard,
            max_drawdown_abs_hard=safety.max_drawdown_abs_hard,
            spot_liquidate_to_quote=safety.spot_liquidate_to_quote,
        ),
        days=[
            SafetyDayRow(
                day=day.day,
                baseline=day.baseline,
                baseline_value=day.baseline.value if day.baseline is not None else 0.0,
                worst_loss_abs=day.worst_loss_abs,
                worst_loss_pct=day.worst_loss_pct,
                worst_loss_at=day.worst_loss_at,
                limit_hit=day.limit_hit,
            )
            for day in record.days
        ],
        days_limit_hit=sum(1 for day in record.days if day.limit_hit),
        worst_day=worst_day.day if worst_day is not None else '',
        worst_day_loss_abs=worst_day.worst_loss_abs if worst_day is not None else 0.0,
        worst_day_loss_pct=worst_day.worst_loss_pct if worst_day is not None else 0.0,
        flatten_fired=record.flatten_fired,
        flatten_reason=record.flatten_reason,
        flatten_completed=record.flatten_completed,
        flatten_unconfirmed=list(record.flatten_unconfirmed),
    )


def _limit_used(
    worst_pct: float,
    threshold_pct: float,
    worst_abs: float,
    threshold_abs: float,
) -> Optional[float]:
    """
    How much of a drawdown threshold the worst excursion consumed, as a percentage.

    The percentage and the absolute threshold are two independent limits either of which can
    fire first (#314), so the answer is whichever came CLOSER — reporting only one would
    understate the session on the account where the other one was the binding constraint.

    Args:
        worst_pct: The deepest drawdown as a share of the baseline
        threshold_pct: The configured percentage limit, 0 = not configured
        worst_abs: The deepest drawdown in account currency
        threshold_abs: The configured absolute limit, 0 = not configured

    Returns:
        The share of the nearest configured limit that was used, or None when neither is
        configured — which is a different statement from having used none of one
    """
    used = [worst / threshold * 100.0
            for worst, threshold in ((worst_pct, threshold_pct), (worst_abs, threshold_abs))
            if threshold > 0]
    return max(used) if used else None
