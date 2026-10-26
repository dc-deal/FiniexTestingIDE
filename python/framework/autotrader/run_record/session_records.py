"""
FiniexTestingIDE - Session Records (#476)

What an AutoTrader session writes into its run as it goes, so that what its report would gather at
the end survives a session killed before it:

- `io/trades.jsonl` — every trade the session closes, one line each, the moment it closes: the row
  the trade history serves. The history the report reads keeps only the newest trades.
- `diagnostics/<sink>.jsonl` — every row a diagnostics sink records, as the logic records it,
  beside the table written at the end.
- `io/algo_state_restored.json` — the algo state the session found at its start, taken or refused:
  the store is overwritten while the session runs, so no later read could say what it started from.

A record that can no longer be written is said once and never ends the session.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.builders.trade_history_report_builder import trade_row
from python.framework.reporting.diagnostics_csv_sink import DiagnosticsCsvSink
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.trading_env.portfolio_manager import PortfolioManager
from python.framework.types.persistence_types import RestoreContext, RestoredAlgoState
from python.framework.types.store_member_types import RunEntryMemberId


def open_trade_stream(
    run_dir: Optional[Path],
    run_id: str,
    unit_name: str,
    portfolio: PortfolioManager,
    logger: AbstractLogger,
) -> Optional[RunStreamAppender]:
    """
    Open the session's trade stream and have the portfolio write every trade record into it.

    Args:
        run_dir: The session's run directory; None when the session keeps none
        run_id: Written into the stream's header line
        unit_name: The session's unit, on the header line and on every row
        portfolio: The session's portfolio, which files the trade records
        logger: Where a failed write is said

    Returns:
        The stream, to be closed after the session's last close; None without a run directory
    """
    if run_dir is None:
        return None
    appender = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER).open_stream(
        RunEntryMemberId.TRADES, run_id, logger,
        stops='The trades this session closes are no longer recorded as they close; the report '
              'at its end still lists the newest of them',
        header_fields={'unit_name': unit_name})
    portfolio.add_trade_listener(
        lambda record: appender.write(trade_row(record, unit_name).model_dump(mode='json')))
    return appender


def stream_diagnostics_rows(
    run_dir: Optional[Path],
    run_id: str,
    decision_logic: AbstractDecisionLogic,
    logger: AbstractLogger,
) -> List[RunStreamAppender]:
    """
    Have every diagnostics sink of the logic stream its rows into the session's run directory —
    the sinks declared so far at once, each later one when the logic declares it.

    Args:
        run_dir: The session's run directory; None when the session keeps none
        run_id: Written into each stream's header line
        decision_logic: The session's logic, whose sinks are streamed
        logger: Where a failed write is said

    Returns:
        The streams, one per sink — filled as the logic declares sinks, to be closed at the end
    """
    streams: List[RunStreamAppender] = []
    if run_dir is None:
        return streams
    writer = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER)

    def open_sink_stream(sink: DiagnosticsCsvSink) -> RunStreamAppender:
        stream = writer.open_stream(
            RunEntryMemberId.DIAGNOSTICS_ROWS, run_id, logger,
            stops=f"The rows of the diagnostics sink '{sink.get_name()}' are no longer recorded "
                  f'as they are appended; its table at the end still holds them',
            header_fields={'sink': sink.get_name(), 'columns': sink.get_columns()},
            sink=sink.get_name())
        streams.append(stream)
        return stream

    decision_logic.stream_diagnostics(open_sink_stream)
    return streams


def record_restored_algo_state(
    run_dir: Optional[Path],
    run_id: str,
    snapshot: Dict[str, Any],
    restore_ctx: RestoreContext,
    accepted: bool,
    logger: AbstractLogger,
) -> None:
    """
    Write the algo state the session found at its start into its run, once.

    Args:
        run_dir: The session's run directory; None when the session keeps none
        run_id: The session
        snapshot: The state the store held
        restore_ctx: Its age, as the framework measured it
        accepted: Whether the logic took it
        logger: Where a failed write is said
    """
    if run_dir is None:
        return
    record = RestoredAlgoState(
        run_id=run_id, accepted=accepted,
        saved_at_utc=restore_ctx.saved_at_utc.isoformat(),
        found_at_utc=restore_ctx.now_utc.isoformat(),
        age_seconds=restore_ctx.age_seconds, trading_days=restore_ctx.trading_days,
        snapshot=snapshot)
    try:
        RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER).write_snapshot(
            RunEntryMemberId.ALGO_STATE_RESTORED, record.model_dump_json(indent=2))
    except OSError as error:
        logger.error(
            f'💾 The algo state this session started from could not be written into its run '
            f'({error}) — the session goes on; the state itself was restored or refused as logged')
