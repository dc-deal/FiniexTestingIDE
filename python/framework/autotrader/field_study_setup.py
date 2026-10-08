"""
FiniexTestingIDE - Field Study Setup (#332)

Builds a Field Study session's capture and runs its preflight, so `AutotraderMain.run()` keeps a
call instead of a block — the shape of `cold_start_setup` and `order_event_stream_setup`: one
function that constructs, wires and decides, and hands back what the session holds on to.

The capture is two writers on one file (#566): the recorder for the study's own choreography, and
the projection that copies the live core's order-event stream into it while the executor records.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.reporting.field_study_recorder import FieldStudyRecorder
from python.framework.reporting.field_study_stream_projection import FieldStudyStreamProjection
from python.framework.reporting.io.artifact_specs import FIELD_STUDY_CAPTURE
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.api_perf_types import ApiPerfSnapshot


@dataclass
class FieldStudySetup:
    """
    What a Field Study session keeps after its setup.

    Lives beside its builder rather than in `framework/types/`: the recorder is a live
    collaborator, not data.

    Args:
        proceed: False when the session must not trade — the reason is already in the session log
        recorder: The capture's writer, closed at shutdown; None when no capture was opened
    """
    proceed: bool = True
    recorder: Optional[FieldStudyRecorder] = None


def open_field_study(
    decision_logic: LiveFieldStudy,
    executor: AbstractTradeExecutor,
    reconciler: Optional[Reconciler],
    trading_model: TradingModel,
    run_dir: Path,
    unit_name: str,
    symbol: str,
    logger: AbstractLogger,
) -> FieldStudySetup:
    """
    Open the capture, attach it to the live core's record, and check the account before trading.

    The projection is registered here, before the session-start venue read, so that read is the
    capture's preflight snapshot. Every order line after it is copied from the stream as it is
    written; nothing in the decision logic records an order any more.

    Args:
        decision_logic: The Live Field Study driving this session
        executor: The session's executor — what records every order transition
        reconciler: The venue reconciler, or None when reconciliation is disabled
        trading_model: The account model the session trades
        run_dir: The run's own directory
        unit_name: The session's unit, which the capture names as its profile
        symbol: The traded symbol
        logger: The session channel

    Returns:
        Whether the session may trade, and the open capture
    """
    if trading_model != TradingModel.SPOT:
        # The Field Study assumes SPOT semantics (sell held base, 50/50 funding,
        # order-book flat-preflight). The MARGIN variant (short = margin position,
        # flat = no positions + free margin) lands with #209 — fail fast until then.
        banner = (
            f"FIELD STUDY ABORTED — trading_model '{trading_model.value}' "
            f"is not supported. The Live Field Study currently supports SPOT only "
            f"(Kraken); the MARGIN variant lands with #209."
        )
        # The SESSION channel — the global log never reaches the summary, and an abort the
        # report does not know about is an abort nobody sees.
        logger.error(banner)
        print(f"\n{'=' * 60}\n  ❌ {banner}\n{'=' * 60}\n")
        return FieldStudySetup(proceed=False)

    recorder = FieldStudyRecorder(
        output_path=str(run_dir / FIELD_STUDY_CAPTURE),
        profile=unit_name,
        symbol=symbol,
        phase_ids=decision_logic.get_phase_ids(),
        logger=logger,
    )
    decision_logic.set_recorder(recorder)
    projection = FieldStudyStreamProjection(recorder, unit_name)
    executor.add_order_event_listener(projection)
    executor.add_broker_truth_listener(projection.write_broker_truth)
    try:
        proceed = _preflight(reconciler, logger)
    except Exception:
        # The capture is open and the session never learns of it — close it here, so a venue
        # that cannot be read at the preflight leaves a terminated file behind, not a header.
        recorder.close('preflight failed')
        raise
    return FieldStudySetup(proceed=proceed, recorder=recorder)


def close_field_study(
    recorder: FieldStudyRecorder,
    api_monitor: Optional[ApiPerfMonitor],
    reconciler: Optional[Reconciler],
) -> None:
    """
    End the capture: what reconciliation did, the REST telemetry, then the session-end marker.

    Called after the session-end venue read, which the projection has already copied in. The
    file is closed even when a summary cannot be written.

    Args:
        recorder: The open capture
        api_monitor: The session's REST monitor, or None when it is switched off
        reconciler: The venue reconciler, or None when reconciliation is disabled
    """
    try:
        recorder.record_reconcile_summary(_reconcile_block(reconciler))
        if api_monitor is not None:
            recorder.record_api_perf(_api_perf_block(api_monitor.get_snapshot()))
    finally:
        recorder.close('session end')


def _preflight(reconciler: Optional[Reconciler], logger: AbstractLogger) -> bool:
    """
    Refuse to trade beside a resting order (#332 / #151).

    The Field Study is funded with assets on both sides (e.g. ~50/50 base/quote) so the SELL
    phases sell held base — a non-quote balance is therefore EXPECTED, not a contaminant. The hard
    requirement is only: no resting broker orders, which would contaminate the run. What the venue
    held is written by the session-start read that follows, as the capture's preflight snapshot.

    Args:
        reconciler: The venue reconciler, or None when reconciliation is disabled
        logger: The session channel

    Returns:
        True if clear (or reconciliation disabled), False to abort the run
    """
    if reconciler is None:
        logger.warning('Field Study preflight skipped — reconciliation is disabled')
        return True

    flat = reconciler.is_account_flat()
    if flat.open_orders:
        banner = (
            f'FIELD STUDY ABORTED — {len(flat.open_orders)} resting broker order(s) '
            f'present; cancel them before the run'
        )
        logger.error(banner)
        print(f"\n{'=' * 60}\n  ❌ {banner}\n{'=' * 60}\n")
        return False

    logger.info(
        f"✅ Field Study preflight: no resting orders "
        f"(starting balances: {flat.asset_balances or 'quote-only'})"
    )
    print('  ▸ Field Study preflight: no resting orders')
    return True


def _reconcile_block(reconciler: Optional[Reconciler]) -> Dict[str, Any]:
    """
    What the session's reconciliation did, as the capture's `reconcile_summary` block.

    Args:
        reconciler: The venue reconciler, or None when reconciliation is disabled

    Returns:
        `enabled`, and where it ran its cycles, its skipped cycles, and the divergences it saw —
        summed over every cycle, so one divergence counts once per cycle it stood
    """
    if reconciler is None:
        return {'enabled': False}
    counters = reconciler.get_display_counters()
    return {
        'enabled': True,
        'cycles': counters['reconcile_count'],
        'skipped': counters['reconcile_skipped'],
        'divergences_seen': counters['reconcile_total_divergences'],
    }


def _api_perf_block(snapshot: ApiPerfSnapshot) -> Dict[str, Any]:
    """
    The REST telemetry as the capture's `api_perf` block.

    Args:
        snapshot: The monitor's per-endpoint state

    Returns:
        Per endpoint its calls, latency and errors, with the session's slow-call and error totals
    """
    return {
        'endpoints': [
            {
                'endpoint': stats.endpoint,
                'calls': stats.count,
                'avg_ms': stats.avg_ms,
                'min_ms': stats.min_ms,
                'max_ms': stats.max_ms,
                'errors': stats.error_count,
            }
            for stats in snapshot.endpoints
        ],
        'slow_calls': snapshot.slow_count,
        'errors': snapshot.total_errors,
    }
