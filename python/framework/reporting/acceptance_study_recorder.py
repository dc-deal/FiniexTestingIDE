"""
FiniexTestingIDE - Acceptance Study Recorder (#332, #358)

Writes an acceptance study's run as analysis-ready JSONL: one JSON object per line,
append-only, flushed per event (crash-safe and tail-able during the live run). The
first line is a header that describes the run. Every event carries a stable shared key
set so a `jq` one-liner or a 3-line pandas load is enough. An order line joins the session's
order-event stream by `extra.stream_seq`, and the lines of one submission share
`extra.submitted_seq` (absent on a refusal made before sending, which was never submitted).

The recorder writes the study's own choreography: the header, every phase's start and result,
the REST telemetry at the end and the session-end marker. Its order and venue lines are the live
core's own record, copied in while the executor writes it (`AcceptanceStudyStreamProjection`,
#566) — the study keeps no second picture of its orders. Both go through this one writer and one
sequence. Every acceptance study writes through it; what differs between them is the file and
the step that follows a finished capture.
"""

from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.types.acceptance_study_types.study_capture_types import (
    SESSION_END_PHASE,
    CaptureCloseReason,
    CaptureEvent,
    CaptureHeader,
    CaptureLineType,
    status_fits_line,
)
from python.framework.types.acceptance_study_types.study_phase_types import PhaseResult
from python.framework.types.log_layout_types import ACCEPTANCE_CAPTURE_SCHEMA_VERSION
from python.framework.types.trading_env_types.order_event_types import OrderEventPlane
from python.framework.types.trading_env_types.order_types import FeeReport


def _utc_now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string (timezone-aware)."""
    return datetime.now(timezone.utc).isoformat()


class AcceptanceStudyRecorder:
    """
    JSONL writer for a single acceptance study run.

    Args:
        output_path: Target .jsonl file (parent dirs are created)
        profile: AutoTrader profile name
        symbol: Traded symbol
        phase_ids: Ordered phase ids (for the header)
        logger: Session logger (for the file-path banner, and a write that fails)
        study_label: The study's name in the session log ('Field Study')
        next_step: What the operator runs once the capture is complete, said when it closes
        fee_report: Where the session's venue reports the fee of a fill, as its adapter
            declares it (#508); None leaves it out of the header
        venue_fee_expected: Whether the session's fills should carry the venue's own fee, as
            the executor answers it (#508); None leaves it out of the header
    """

    def __init__(
        self,
        output_path: str,
        profile: str,
        symbol: str,
        phase_ids: List[str],
        logger: ScenarioLogger,
        study_label: str,
        next_step: str,
        fee_report: Optional[FeeReport] = None,
        venue_fee_expected: Optional[bool] = None,
    ):
        self._path = Path(output_path)
        self._logger = logger
        self._study_label = study_label
        self._next_step = next_step
        self._seq = 0
        self._phase = ''
        self._phase_index = -1
        self._failed = False

        self._writer = JsonlStreamWriter(self._path)

        header = CaptureHeader(
            schema_version=ACCEPTANCE_CAPTURE_SCHEMA_VERSION,
            started_utc=_utc_now_iso(),
            profile=profile,
            symbol=symbol,
            phases=list(phase_ids),
            fee_report=fee_report,
            venue_fee_expected=venue_fee_expected,
        )
        line = asdict(header)
        # The line is where the declaration becomes its value — the header keeps the enum
        line['fee_report'] = fee_report.value if fee_report is not None else None
        self._write_line(line)
        self._logger.info(f'📝 {self._study_label} recorder → {self._path}')

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

    def get_phase(self) -> Tuple[str, int]:
        """
        The phase in progress — what a submission recorded now is filed under.

        Returns:
            (phase id, phase index); ('', -1) before the first phase starts
        """
        return self._phase, self._phase_index

    def record_phase_start(
        self,
        phase_id: str,
        phase_index: int,
        side: Optional[str],
        phase_type: Optional[str] = None,
    ) -> None:
        """
        Record a phase-start marker and switch the phase context.

        Args:
            phase_id: Phase id
            phase_index: Phase index
            side: Trade side ('LONG'/'SHORT'/None)
            phase_type: The phase's kind, in its study's own set — what a phase whose result is
                meant never to arrive is recognised by
        """
        self.set_phase(phase_id, phase_index)
        self._emit(OrderEventPlane.BOT, CaptureLineType.PHASE_START, side=side,
                   extra={'phase_type': phase_type} if phase_type else None)

    def record_phase_result(self, result: PhaseResult, extra: Optional[Dict[str, Any]] = None) -> None:
        """
        Record a phase outcome (PASS/FAIL/SKIPPED/EXPECTED_REJECTION).

        Args:
            result: Completed PhaseResult from the phase machine
            extra: Further fields a study records with its outcome, beside the shared three
        """
        self._emit(
            OrderEventPlane.BOT, CaptureLineType.PHASE_RESULT,
            status=result.outcome,
            extra={
                'reason': result.reason,
                'phase_type': result.phase_type.value,
                'rearm_attempts': result.rearm_attempts,
                **(extra or {}),
            },
        )

    # ============================================
    # Lines copied from the live core's record (#566)
    # ============================================

    def write_projected(
        self,
        plane: OrderEventPlane,
        event_type: CaptureLineType,
        phase: str,
        phase_index: int,
        **fields: Any,
    ) -> None:
        """
        Write one line the projection copied from the order-event stream.

        Filed under the phase it names rather than the one in progress: an order line belongs to
        the phase that submitted the order, a venue read at the session's start and end to its
        own marker phase.

        Args:
            plane: The bot's account for an order line, the venue's for a venue read
            event_type: The line's type
            phase: The phase it is filed under
            phase_index: Its index; -1 for a marker phase
            **fields: Further CaptureEvent fields
        """
        status = fields.get('status')
        if not status_fits_line(event_type, status):
            # Reported, never raised: this runs inside the executor's listener, and a capture
            # defect must not end a real-money session. The line is written as it came.
            self._logger.error(
                f'❌ {self._study_label} capture: a `{event_type}` line carries the status '
                f'{status!r}, which is not in the set its type declares.')
        self._seq += 1
        event = CaptureEvent(
            ts_utc=_utc_now_iso(),
            seq=self._seq,
            plane=plane,
            event_type=event_type,
            phase=phase,
            phase_index=phase_index,
            **fields,
        )
        self._write_line(_line_of(event))

    # ============================================
    # Telemetry and lifecycle
    # ============================================

    def record_reconcile_summary(self, summary: Dict[str, Any]) -> None:
        """
        Record what the session's reconciliation did, once at its end.

        The reconciliation lines in between are written only when the picture CHANGED, so
        without this a capture with none of them could mean a clean session, a session whose
        every cycle was skipped, or one that never reconciled at all.

        Args:
            summary: Whether reconciliation ran, its cycles, its skipped cycles and the
                divergences it saw
        """
        self.write_projected(
            OrderEventPlane.BOT, CaptureLineType.RECONCILE_SUMMARY, SESSION_END_PHASE, -1,
            reconcile=summary)

    def record_api_perf(self, snapshot: Dict[str, Any]) -> None:
        """
        Record the session's broker REST telemetry, once at its end (#351).

        Args:
            snapshot: Per-endpoint calls, latency and errors, with the session's totals
        """
        self.write_projected(
            OrderEventPlane.BOT, CaptureLineType.API_PERF, SESSION_END_PHASE, -1,
            api_perf=snapshot)

    def close(self, reason: CaptureCloseReason = CaptureCloseReason.SESSION_END) -> None:
        """
        Write the session-end marker and close the file.

        Args:
            reason: Why the capture closes
        """
        if not self._writer.is_open():
            return
        self.write_projected(
            OrderEventPlane.BOT, CaptureLineType.SESSION_END, SESSION_END_PHASE, -1,
            status=reason)
        self._writer.close()

        # Operator next-step hint. The capture (this JSONL) is the durable, expensive
        # artifact; the certificate is a separate, free, re-runnable judgment over it.
        # Surfacing the next step here keeps a finished study from being left
        # un-certified — without a study special case in the core shutdown path.
        self._logger.info(
            f'✅ {self._study_label} capture complete → {self._path}\n'
            f'   Next step (no live trade): {self._next_step}'
        )

    def get_path(self) -> Path:
        """Return the JSONL output path."""
        return self._path

    # ============================================
    # Internals
    # ============================================

    def _emit(self, plane: OrderEventPlane, event_type: CaptureLineType, **fields: Any) -> None:
        """Build a CaptureEvent with the current phase context and write it."""
        self.write_projected(plane, event_type, self._phase, self._phase_index, **fields)

    def _write_line(self, obj: Dict[str, Any]) -> None:
        """
        Serialize one record as a JSON line, dropping None fields, and flush.

        The first failed write is reported on the session channel and ends the writing: the
        capture is incomplete from there on, and a real-money session is not stopped for it.

        Args:
            obj: The line
        """
        if self._failed:
            return
        try:
            self._writer.write(obj)
        except OSError as e:
            self._failed = True
            self._logger.error(
                f'❌ The {self._study_label} capture {self._path} could not be written: {e}. It '
                f'is incomplete from this line on; the session continues.')


def _line_of(event: CaptureEvent) -> Dict[str, Any]:
    """
    The line an event becomes — where its enums turn into the values the JSON carries.

    Args:
        event: The typed line

    Returns:
        The line as a dict of JSON-serializable values
    """
    line = asdict(event)
    for key in ('plane', 'event_type', 'status', 'detected_via'):
        value = line[key]
        if isinstance(value, Enum):
            line[key] = value.value
    return line
