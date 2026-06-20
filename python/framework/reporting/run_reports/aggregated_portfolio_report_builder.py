"""
Aggregated-portfolio report builder (#397) — the per-currency detail-view postprocessor.

Groups the already-built per-scenario portfolio rows by account currency and rolls each group up
into the rich `AggregatedPortfolioReport` (the view `PortfolioAggregator` used to feed inline).
Built **from the model rows** (portfolio + execution + pending per-unit rows), not by re-reading
`ProcessResult` — every aggregation input already lives on those rows (the per-unit values), joined
by unit name. For a mixed batch (margin + spot in one currency) the group is also split into
`margin` / `spot` sub-aggregates for the executive block. `RunSummary` stays the lean headline.
"""

from typing import Dict, List

from python.framework.reporting.run_reports.report_aggregators import aggregate_full_portfolio
from python.framework.types.api.report_types import (
    AggregatedPortfolioCurrency, AggregatedPortfolioReport, ExecutionStatsReport,
    PendingOrdersReport, PortfolioReport, PortfolioUnitRow)


def build_aggregated_portfolio_report(
    portfolio_report: PortfolioReport,
    execution_report: ExecutionStatsReport,
    pending_report: PendingOrdersReport,
) -> AggregatedPortfolioReport:
    """
    Build the aggregated per-currency portfolio report from the per-unit model rows.

    Args:
        portfolio_report: per-scenario portfolio rows (currency + balances + costs + spot)
        execution_report: per-scenario order counts (joined by unit name)
        pending_report: per-scenario pending stats incl. latency count (joined by unit name)

    Returns:
        AggregatedPortfolioReport — one currency group each (combined + margin/spot for mixed)
    """
    exec_by_name = {r.name: r for r in execution_report.units}
    pending_by_name = {r.name: r for r in pending_report.units}

    groups: Dict[str, List[PortfolioUnitRow]] = {}
    for row in portfolio_report.units:
        groups.setdefault(row.currency, []).append(row)

    currencies = []
    for currency in sorted(groups):
        rows = groups[currency]
        margin_rows = [r for r in rows if not r.spot_mode]
        spot_rows = [r for r in rows if r.spot_mode]
        is_mixed = bool(margin_rows) and bool(spot_rows)
        is_spot = bool(spot_rows) and not margin_rows

        combined = aggregate_full_portfolio(
            rows, exec_by_name, pending_by_name, currency, is_spot, '')
        margin = aggregate_full_portfolio(
            margin_rows, exec_by_name, pending_by_name, currency, False, 'Margin') if is_mixed else None
        spot = aggregate_full_portfolio(
            spot_rows, exec_by_name, pending_by_name, currency, True, 'Spot') if is_mixed else None

        currencies.append(AggregatedPortfolioCurrency(
            currency=currency,
            scenario_count=len(rows),
            scenario_names=[r.name for r in rows],
            is_spot=is_spot,
            is_mixed=is_mixed,
            combined=combined,
            margin=margin,
            spot=spot,
        ))
    return AggregatedPortfolioReport(currencies=currencies)
