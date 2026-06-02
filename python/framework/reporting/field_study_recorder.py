"""
FiniexTestingIDE - Field Study Recorder (#332, CORE)

Writes the Field Study run as analysis-ready JSONL: one JSON object per line,
append-only, flushed per event (crash-safe and tail-able during the live run). The
first line is a header that describes the run. Every event carries a stable shared key
set so a `jq` one-liner or a 3-line pandas load is enough — and two planes (bot-observed
vs. broker-truth) join on phase + order_id.

The recorder is source-agnostic about who feeds it: the LiveFieldStudy decision logic
records the bot plane (phase boundaries + #348 order events with phase context); the
AutoTrader wiring records the broker-truth plane (Reconciler broker-truth pulls,
reconcile alerts) and telemetry sub-blocks (#340 slippage, #351 API perf).
"""

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.field_study_types import (
    FieldStudyEvent,
    FieldStudyHeader,
    PhaseResult,
)

_SCHEMA_VERSION = '1.0'

# Plane labels — keep in sync with the certificate analyzer and the operator guide.
PLANE_BOT = 'bot'
PLANE_BROKER_TRUTH = 'broker_truth'


def _utc_now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string (timezone-aware)."""
    return datetime.now(timezone.utc).isoformat()


class FieldStudyRecorder:
    """
    JSONL writer for a single Field Study run.

    Args:
        output_path: Target .jsonl file (parent dirs are created)
        profile: AutoTrader profile name
        symbol: Traded symbol
        release_target: Release version this run certifies (or 'dev')
        phase_ids: Ordered phase ids (for the header)
        logger: Session logger (for the file-path banner)
    """

    def __init__(
        self,
        output_path: str,
        profile: str,
        symbol: str,
        release_target: str,
        phase_ids: List[str],
        logger: ScenarioLogger,
    ):
        self._path = Path(output_path)
        self._logger = logger
        self._seq = 0
        self._phase = ''
        self._phase_index = -1

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, 'w', encoding='utf-8')

        header = FieldStudyHeader(
            schema_version=_SCHEMA_VERSION,
            started_utc=_utc_now_iso(),
            profile=profile,
            symbol=symbol,
            release_target=release_target,
            phases=list(phase_ids),
        )
        self._write_line(asdict(header))
        self._logger.info(f"📝 Field Study recorder → {self._path}")

    # ============================================
    # Phase context
    # ============================================

    def set_phase(self, phase_id: str, phase_index: int) -> None:
        """
        Set the current phase context applied to subsequent events.

        Args:
            phase_id: Current phase id
            phase_index: Current phase index
        """
        self._phase = phase_id
        self._phase_index = phase_index

    def record_phase_start(self, phase_id: str, phase_index: int, side: Optional[str]) -> None:
        """
        Record a phase-start marker and switch the phase context.

        Args:
            phase_id: Phase id
            phase_index: Phase index
            side: Trade side ('LONG'/'SHORT'/None)
        """
        self.set_phase(phase_id, phase_index)
        self._emit(PLANE_BOT, 'phase_start', side=side)

    def record_phase_result(self, result: PhaseResult) -> None:
        """
        Record a phase outcome (PASS/FAIL/SKIPPED/EXPECTED_REJECTION).

        Args:
            result: Completed PhaseResult from the phase machine
        """
        self._emit(
            PLANE_BOT, 'phase_result',
            status=result.outcome.value,
            extra={
                'reason': result.reason,
                'phase_type': result.phase_type.value,
                'rearm_attempts': result.rearm_attempts,
            },
        )

    # ============================================
    # Bot plane — #348 order events (with phase context)
    # ============================================

    def record_order_event(
        self,
        event_type: str,
        order_id: Optional[str] = None,
        side: Optional[str] = None,
        lots: Optional[float] = None,
        price: Optional[float] = None,
        status: Optional[str] = None,
        detected_via: str = 'poll',
        slippage: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a bot-observed order/lifecycle event (fill, rejection, cancel, partial).

        Args:
            event_type: 'order_filled' / 'order_rejected' / 'order_cancelled' / 'partial_close'
            order_id: Internal order/position id
            side: 'LONG'/'SHORT'
            lots: Executed/observed lots
            price: Fill/limit price
            status: Observed status string
            detected_via: 'poll' today, 'push' once #331 lands
            slippage: Submission slippage block (#340), when available
            extra: Event-specific overflow fields
        """
        self._emit(
            PLANE_BOT, event_type,
            order_id=order_id, side=side, lots=lots, price=price,
            status=status, detected_via=detected_via, slippage=slippage, extra=extra,
        )

    # ============================================
    # Broker-truth plane — pulled from the broker (#151)
    # ============================================

    def record_broker_truth(
        self,
        order_count: int,
        balances: Dict[str, float],
        is_flat: bool,
        reconcile: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record a broker-truth snapshot (resting orders + balances + flat status).

        Args:
            order_count: Resting broker orders right now
            balances: Broker balances by asset
            is_flat: Whether the account is flat by broker truth
            reconcile: Reconciliation counters block (#151), when available
        """
        self._emit(
            PLANE_BROKER_TRUTH, 'broker_snapshot',
            status='flat' if is_flat else 'not_flat',
            reconcile=reconcile,
            extra={'order_count': order_count, 'balances': balances},
        )

    def record_reconcile_alert(self, alert: Dict[str, Any]) -> None:
        """
        Record a reconciliation divergence alert (#151 ALERT_ONLY).

        Args:
            alert: Divergence detail block
        """
        self._emit(PLANE_BROKER_TRUTH, 'reconcile_alert', reconcile=alert)

    def record_api_perf(self, snapshot: Dict[str, Any]) -> None:
        """
        Record an API performance snapshot (#351) as forensic context.

        Args:
            snapshot: Per-endpoint latency/error block
        """
        self._emit(PLANE_BOT, 'api_perf', api_perf=snapshot)

    # ============================================
    # Lifecycle
    # ============================================

    def close(self, reason: str = 'session end') -> None:
        """
        Write the session-end marker and close the file.

        Args:
            reason: Human-readable end reason
        """
        if self._fh is None:
            return
        self._emit(PLANE_BOT, 'session_end', status=reason)
        self._fh.close()
        self._fh = None

    def get_path(self) -> Path:
        """Return the JSONL output path."""
        return self._path

    # ============================================
    # Internals
    # ============================================

    def _emit(self, plane: str, event_type: str, **fields: Any) -> None:
        """Build a FieldStudyEvent with the current phase context and write it."""
        self._seq += 1
        event = FieldStudyEvent(
            ts_utc=_utc_now_iso(),
            seq=self._seq,
            plane=plane,
            event_type=event_type,
            phase=self._phase,
            phase_index=self._phase_index,
            **fields,
        )
        self._write_line(asdict(event))

    def _write_line(self, obj: Dict[str, Any]) -> None:
        """Serialize one record as a JSON line, dropping None fields, and flush."""
        if self._fh is None:
            return
        compact = {k: v for k, v in obj.items() if v is not None}
        self._fh.write(json.dumps(compact) + '\n')
        self._fh.flush()
