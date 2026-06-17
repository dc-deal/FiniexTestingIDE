"""
Scenario-details report builder (#391/#393) — the per-scenario execution/signal postprocessor.

Maps each scenario's `ProcessResult` (+ its index-synced `SingleScenario`) to a
`ScenarioDetailsRow`: status (success/failed/hybrid), execution metadata, tick range, and the
decision-logic signal counts. **Sim-only** (AutoTrader has no scenario grid) and reads the
batch directly — NOT via `RunUnit`, because failed scenarios carry no `tick_loop_results` yet
must still appear (the section is the full scenario status view).
"""

from python.framework.types.api.report_types import ScenarioDetailsReport, ScenarioDetailsRow
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


def build_scenario_details_report_from_batch(
    batch: BatchExecutionSummary) -> ScenarioDetailsReport:
    """
    Build the report from a sim batch — one row per scenario (incl. failed).

    Args:
        batch: The completed batch summary

    Returns:
        ScenarioDetailsReport with one row per scenario
    """
    rows = [
        _to_row(result, batch.get_scenario_by_process_result(result))
        for result in batch.process_result_list
    ]
    return ScenarioDetailsReport(units=rows)


def _to_row(result: ProcessResult, scenario: SingleScenario) -> ScenarioDetailsRow:
    """Map one ProcessResult (+ scenario) to a row — success / failed / hybrid."""
    has_error = bool(result.error_type or result.error_message)
    common = dict(
        name=result.scenario_name,
        symbol=scenario.symbol,
        data_source=scenario.data_broker_type,
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
