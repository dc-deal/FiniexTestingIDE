"""
Scenario-details report builder (#391/#393) — the per-scenario execution/signal postprocessor.

Maps each scenario's `ProcessResult` (+ its index-synced `SingleScenario`) to a
`ScenarioDetailsRow`: status (success/failed/hybrid), execution metadata, tick range, and the
decision-logic signal counts. **Sim-only** (AutoTrader has no scenario grid) and reads the
batch directly — NOT via `RunUnit`, because failed scenarios carry no `tick_loop_results` yet
must still appear (the section is the full scenario status view).
"""

from typing import Dict

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.types.api.report_types import (
    DataBrokerRow,
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
    pairs = [(result, batch.get_scenario_by_process_result(result))
             for result in batch.process_result_list]
    market_types = _market_types({scenario.data_broker_type for _, scenario in pairs})
    rows = [_to_row(result, scenario, market_types) for result, scenario in pairs]
    return ScenarioDetailsReport(
        run_id=run_id, units=rows, data_brokers=_data_brokers(rows, market_types))


def _market_types(broker_types: set) -> Dict[str, str]:
    """
    What each broker a run read from IS, resolved once for the rows and the roll-up alike.

    Args:
        broker_types: The data broker types the run's scenarios name

    Returns:
        broker type → market type
    """
    market_config = MarketConfigManager()
    return {broker_type: market_config.get_market_type(broker_type).value
            for broker_type in broker_types}


def _data_brokers(rows: list, market_types: Dict[str, str]) -> list:
    """
    Roll the scenario rows up per data broker, resolving what each broker IS exactly once.

    Its own stage rather than something a renderer does on the way past (§12): the console,
    the JSON artifact and the API all want this grouping, and three groupings are three
    chances to disagree. The market type is resolved HERE from its authoritative owner —
    a PRESENT renderer must never instantiate a config manager, and this one used to, which
    meant the console reported what a broker is TODAY beside figures produced under whatever
    it was then.

    Args:
        rows: The run's scenario rows, failed ones included
        market_types: broker type → market type, resolved once for the whole report

    Returns:
        One row per data broker, sorted by broker type
    """
    grouped: dict = {}
    for row in rows:
        entry = grouped.setdefault(
            row.data_broker_type, {'symbols': set(), 'count': 0, 'bases': []})
        entry['count'] += 1
        entry['symbols'].add(row.symbol)
        # Already joined at the row; split again so the roll-up de-duplicates across
        # scenarios rather than concatenating their strings.
        entry['bases'].extend(b for b in row.price_bases.split(',') if b)
    return [
        DataBrokerRow(
            data_broker_type=broker_type,
            market_type=market_types[broker_type],
            scenario_count=entry['count'],
            symbols=sorted(entry['symbols']),
            price_bases=joined_distinct(entry['bases']),
        )
        for broker_type, entry in sorted(grouped.items())
    ]


def _to_row(result: ProcessResult, scenario: SingleScenario,
            market_types: Dict[str, str]) -> ScenarioDetailsRow:
    """Map one ProcessResult (+ scenario) to a row — success / failed / hybrid."""
    has_error = bool(result.error_type or result.error_message)
    common = dict(
        name=result.scenario_name,
        symbol=scenario.symbol,
        data_broker_type=scenario.data_broker_type,
        market_type=market_types[scenario.data_broker_type],
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
        worker_count=len((scenario.strategy_config or {}).get('worker_instances') or {}),
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
    # Only a tracker counts decisions; without one the counters are unknown, not zero.
    counted = decision is not None and decision.tracked
    return ScenarioDetailsRow(
        status='hybrid' if has_error else 'success',
        ticks_processed=coordination.ticks_processed,
        first_tick_time=tick_range.first_tick_time.isoformat() if tick_range.first_tick_time else '',
        last_tick_time=tick_range.last_tick_time.isoformat() if tick_range.last_tick_time else '',
        # No tick processed is no market time — 0.0, as the row declares, never None.
        tick_timespan_seconds=tick_range.tick_timespan_seconds or 0.0,
        buy_signals=decision.buy_signals if counted else None,
        sell_signals=decision.sell_signals if counted else None,
        flat_signals=decision.flat_signals if counted else None,
        trades_requested=decision.trades_requested if counted else None,
        **common,
    )
