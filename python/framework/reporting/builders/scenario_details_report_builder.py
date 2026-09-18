"""
Scenario-details report builder (#391/#393) — the per-scenario execution/signal postprocessor.

Maps each scenario's `ProcessResult` (+ its index-synced `SingleScenario`) to a
`ScenarioDetailsRow`: status (success/failed/hybrid), execution metadata, tick range, and the
decision-logic signal counts. **Sim-only** (AutoTrader has no scenario grid) and reads the
batch directly — NOT via `RunUnit`, because failed scenarios carry no `tick_loop_results` yet
must still appear (the section is the full scenario status view).
"""

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.api.report_types import (
    DataSourceRow,
    ScenarioDetailsReport,
    ScenarioDetailsRow,
)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.data_origin_types import joined_distinct
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


def build_scenario_details_report_from_batch(
    run_id: str,
    batch: BatchExecutionSummary) -> ScenarioDetailsReport:
    """
    Build the report from a sim batch — one row per scenario (incl. failed).

    Args:
        run_id: The run this report belongs to
        batch: The completed batch summary

    Returns:
        ScenarioDetailsReport with one row per scenario
    """
    rows = [
        _to_row(result, batch.get_scenario_by_process_result(result))
        for result in batch.process_result_list
    ]
    return ScenarioDetailsReport(
        run_id=run_id, units=rows, data_sources=_data_sources(rows))


def _data_sources(rows: list) -> list:
    """
    Roll the scenario rows up per data source, resolving what each source IS exactly once.

    Its own stage rather than something a renderer does on the way past (§12): the console,
    the JSON artifact and the API all want this grouping, and three groupings are three
    chances to disagree. The market type is resolved HERE from its authoritative owner —
    a PRESENT renderer must never instantiate a config manager, and this one used to, which
    meant the console reported what a broker is TODAY beside figures produced under whatever
    it was then.

    Args:
        rows: The run's scenario rows, failed ones included

    Returns:
        One row per data source, sorted by broker type
    """
    market_config = MarketConfigManager()
    grouped: dict = {}
    for row in rows:
        entry = grouped.setdefault(
            row.data_source, {'symbols': set(), 'count': 0, 'bases': []})
        entry['count'] += 1
        entry['symbols'].add(row.symbol)
        # Already joined at the row; split again so the roll-up de-duplicates across
        # scenarios rather than concatenating their strings.
        entry['bases'].extend(b for b in row.price_bases.split(',') if b)
    return [
        DataSourceRow(
            broker_type=broker_type,
            market_type=market_config.get_market_type(broker_type).value,
            scenario_count=entry['count'],
            symbols=sorted(entry['symbols']),
            price_bases=joined_distinct(entry['bases']),
        )
        for broker_type, entry in sorted(grouped.items())
    ]


def _to_row(result: ProcessResult, scenario: SingleScenario) -> ScenarioDetailsRow:
    """Map one ProcessResult (+ scenario) to a row — success / failed / hybrid."""
    has_error = bool(result.error_type or result.error_message)
    common = dict(
        name=result.scenario_name,
        symbol=scenario.symbol,
        data_source=scenario.data_broker_type,
        # In `common`, so a FAILED row carries it too: a scenario that failed over development
        # data and one that failed over production data are different failures, and this row is
        # the only per-scenario place that distinction survives the run.
        data_format_versions=joined_distinct(scenario.data_format_versions),
        origin_classes=joined_distinct(scenario.origin_classes),
        origin_evidence_grades=joined_distinct(scenario.origin_evidence_grades),
        price_bases=joined_distinct(scenario.price_bases),
        account_currency=scenario.account_currency or '',
        account_currency_explicit=bool(
            (scenario.trade_simulator_config or {}).get('account_currency')),
        execution_time_ms=getattr(result, 'execution_time_ms', 0.0) or 0.0,
        error_type=result.error_type or '',
        error_message=result.error_message or '',
    )

    tick_loop = getattr(result, 'tick_loop_results', None)
    if tick_loop is None:
        # Pure failure — preparation/validation failed, no execution data
        return ScenarioDetailsRow(status='failed', **common)

    decision = tick_loop.decision_statistics
    coordination = tick_loop.coordination_statistics
    tick_range = tick_loop.tick_range_stats
    return ScenarioDetailsRow(
        status='hybrid' if has_error else 'success',
        ticks_processed=coordination.ticks_processed,
        first_tick_time=tick_range.first_tick_time.isoformat() if tick_range.first_tick_time else '',
        last_tick_time=tick_range.last_tick_time.isoformat() if tick_range.last_tick_time else '',
        tick_timespan_seconds=tick_range.tick_timespan_seconds,
        buy_signals=decision.buy_signals,
        sell_signals=decision.sell_signals,
        flat_signals=decision.flat_signals,
        trades_requested=decision.trades_requested,
        worker_count=len(tick_loop.worker_statistics),
        **common,
    )
