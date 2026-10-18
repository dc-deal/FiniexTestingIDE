"""
FiniexTestingIDE - Acceptance Study Setup (#332, #358)

Builds an acceptance study session's capture and runs its preflight, so `AutotraderMain.run()`
keeps a call instead of a block — the shape of `cold_start_setup` and `order_event_stream_setup`:
one function that constructs, wires and decides, and hands back what the session holds on to.

The capture is two writers on one file (#566): the recorder for the study's own choreography, and
the projection that copies the live core's order-event stream into it while the executor records.
Every acceptance study is set up here; what differs between them — its name, its capture file, the
step after it — the study declares itself.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from python.framework.acceptance_studies.abstract_acceptance_study import AbstractAcceptanceStudy
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.acceptance_study_recorder import AcceptanceStudyRecorder
from python.framework.reporting.acceptance_study_stream_projection import (
    AcceptanceStudyStreamProjection,
)
from python.framework.reporting.api_perf_monitor import ApiPerfMonitor
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.reconciler import Reconciler
from python.framework.types.acceptance_study_types.study_capture_types import CaptureCloseReason
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.api_perf_types import ApiPerfSnapshot


@dataclass
class AcceptanceStudySetup:
    """
    What an acceptance study session keeps after its setup.

    Lives beside its builder rather than in `framework/types/`: the recorder is a live
    collaborator, not data.

    Args:
        proceed: False when the session must not trade — the reason is already in the session log
        recorder: The capture's writer, closed at shutdown; None when no capture was opened
        refusal: Why the session must not trade, as the session log says it — '' when it may
    """
    proceed: bool = True
    recorder: Optional[AcceptanceStudyRecorder] = None
    refusal: str = ''


def open_acceptance_study(
    decision_logic: AbstractAcceptanceStudy,
    executor: AbstractTradeExecutor,
    reconciler: Optional[Reconciler],
    trading_model: TradingModel,
    run_dir: Path,
    unit_name: str,
    symbol: str,
    logger: AbstractLogger,
) -> AcceptanceStudySetup:
    """
    Open the capture, attach it to the live core's record, and check the account before trading.

    The projection is registered here, before the session-start venue read, so that read is the
    capture's preflight snapshot. Every order line after it is copied from the stream as it is
    written; nothing in the decision logic records an order any more.

    Args:
        decision_logic: The acceptance study driving this session
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
    label = decision_logic.STUDY_LABEL
    if trading_model != TradingModel.SPOT:
        # The acceptance studies assume SPOT semantics (sell held base, funding on both sides,
        # order-book flat-preflight). The MARGIN variant (short = margin position,
        # flat = no positions + free margin) lands with #209 — fail fast until then.
        banner = (
            f"{label.upper()} ABORTED — trading_model '{trading_model.value}' "
            f"is not supported. The {label} currently supports SPOT only "
            f"(Kraken); the MARGIN variant lands with #209."
        )
        # The SESSION channel — the global log never reaches the summary, and an abort the
        # report does not know about is an abort nobody sees.
        logger.error(banner)
        print(f"\n{'=' * 60}\n  ❌ {banner}\n{'=' * 60}\n")
        return AcceptanceStudySetup(proceed=False, refusal=banner)

    recorder = AcceptanceStudyRecorder(
        output_path=str(run_dir / decision_logic.get_capture_name()),
        profile=unit_name,
        symbol=symbol,
        phase_ids=decision_logic.get_phase_ids(),
        logger=logger,
        study_label=label,
        next_step=decision_logic.get_capture_next_step(),
        # What the session's adapter declares, and what the executor makes of it when it books
        # a fill — the certificate judges an estimated fee against the executor's answer, never
        # against a config or a second copy of its rule
        fee_report=executor.broker.adapter.get_order_capabilities().fee_report,
        venue_fee_expected=executor.expects_venue_fee_in_fill_answer(),
    )
    decision_logic.set_recorder(recorder)
    projection = AcceptanceStudyStreamProjection(recorder, unit_name)
    executor.add_order_event_listener(projection)
    executor.add_broker_truth_listener(projection.write_broker_truth)
    try:
        refusal = _preflight(reconciler, logger, label)
    except Exception:
        # The capture is open and the session never learns of it — close it here, so a venue
        # that cannot be read at the preflight leaves a terminated file behind, not a header.
        recorder.close(CaptureCloseReason.PREFLIGHT_FAILED)
        raise
    return AcceptanceStudySetup(proceed=not refusal, recorder=recorder, refusal=refusal)


def close_acceptance_study(
    recorder: AcceptanceStudyRecorder,
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
        recorder.close(CaptureCloseReason.SESSION_END)


def _preflight(reconciler: Optional[Reconciler], logger: AbstractLogger, label: str) -> str:
    """
    Refuse to trade beside a resting order (#332 / #151).

    An acceptance study is funded with assets on both sides (e.g. ~50/50 base/quote) so its SELL
    phases sell held base — a non-quote balance is therefore EXPECTED, not a contaminant. The hard
    requirement is only: no resting broker orders, which would contaminate the run. What the venue
    held is written by the session-start read that follows, as the capture's preflight snapshot.

    Args:
        reconciler: The venue reconciler, or None when reconciliation is disabled
        logger: The session channel
        label: The study's name, as the banner and the log say it

    Returns:
        '' if clear (or reconciliation disabled), else the refusal that aborts the run
    """
    if reconciler is None:
        logger.warning(f'{label} preflight skipped — reconciliation is disabled')
        return ''

    flat = reconciler.is_account_flat()
    if flat.open_orders:
        banner = (
            f'{label.upper()} ABORTED — {len(flat.open_orders)} resting broker order(s) '
            f'present; cancel them before the run'
        )
        logger.error(banner)
        print(f"\n{'=' * 60}\n  ❌ {banner}\n{'=' * 60}\n")
        return banner

    logger.info(
        f"✅ {label} preflight: no resting orders "
        f"(starting balances: {flat.asset_balances or 'quote-only'})"
    )
    print(f'  ▸ {label} preflight: no resting orders')
    return ''


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
