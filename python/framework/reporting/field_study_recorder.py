"""
FiniexTestingIDE - Field Study Recorder (#332)

Writes the Field Study run as analysis-ready JSONL: one JSON object per line,
append-only, flushed per event (crash-safe and tail-able during the live run). The
first line is a header that describes the run. Every event carries a stable shared key
set so a `jq` one-liner or a 3-line pandas load is enough. An order line joins the session's
order-event stream by `extra.stream_seq`, and the lines of one submission share
`extra.submitted_seq` (absent on a refusal made before sending, which was never submitted).

The recorder writes the study's own choreography: the header, every phase's start and result,
the REST telemetry at the end and the session-end marker. Its order and venue lines are the live
core's own record, copied in while the executor writes it (`FieldStudyStreamProjection`, #566) —
the study keeps no second picture of its orders. Both go through this one writer and one sequence.
"""

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.types.autotrader_types.field_study_types import (
    FieldStudyEvent,
    FieldStudyHeader,
    PhaseResult,
)

# 2.0 — order and venue lines are projected from the live core's record (#566): they name the
# phase that submitted the order, a full close is a line of its own, a refusal made before sending
# is one too, and a fill carries what its slippage is measured against.
_SCHEMA_VERSION = '2.0'

# Plane labels — keep in sync with the certificate analyzer and the operator guide.
PLANE_BOT = 'bot'
PLANE_BROKER_TRUTH = 'broker_truth'

# The marker phases outside the sequence: the venue's read before trading, and everything written
# once the session ends. The certificate selects the end snapshot by the second, never by position.
PREFLIGHT_PHASE = 'preflight'
SESSION_END_PHASE = 'session_end'


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
        phase_ids: Ordered phase ids (for the header)
        logger: Session logger (for the file-path banner, and a write that fails)
    """

    def __init__(
        self,
        output_path: str,
        profile: str,
        symbol: str,
        phase_ids: List[str],
        logger: ScenarioLogger,
    ):
        self._path = Path(output_path)
        self._logger = logger
        self._seq = 0
        self._phase = ''
        self._phase_index = -1
        self._failed = False

        self._writer = JsonlStreamWriter(self._path)

        header = FieldStudyHeader(
            schema_version=_SCHEMA_VERSION,
            started_utc=_utc_now_iso(),
            profile=profile,
            symbol=symbol,
            phases=list(phase_ids),
        )
        self._write_line(asdict(header))
        self._logger.info(f'📝 Field Study recorder → {self._path}')

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
    # Lines copied from the live core's record (#566)
    # ============================================

    def write_projected(
        self,
        plane: str,
        event_type: str,
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
            plane: 'bot' for an order line, 'broker_truth' for a venue read
            event_type: The line's type
            phase: The phase it is filed under
            phase_index: Its index; -1 for a marker phase
            **fields: Further FieldStudyEvent fields
        """
        self._seq += 1
        event = FieldStudyEvent(
            ts_utc=_utc_now_iso(),
            seq=self._seq,
            plane=plane,
            event_type=event_type,
            phase=phase,
            phase_index=phase_index,
            **fields,
        )
        self._write_line(asdict(event))

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
            PLANE_BOT, 'reconcile_summary', SESSION_END_PHASE, -1, reconcile=summary)

    def record_api_perf(self, snapshot: Dict[str, Any]) -> None:
        """
        Record the session's broker REST telemetry, once at its end (#351).

        Args:
            snapshot: Per-endpoint calls, latency and errors, with the session's totals
        """
        self.write_projected(PLANE_BOT, 'api_perf', SESSION_END_PHASE, -1, api_perf=snapshot)

    def close(self, reason: str = 'session end') -> None:
        """
        Write the session-end marker and close the file.

        Args:
            reason: Human-readable end reason
        """
        if not self._writer.is_open():
            return
        self.write_projected(PLANE_BOT, 'session_end', SESSION_END_PHASE, -1, status=reason)
        self._writer.close()

        # Operator next-step hint. The capture (this JSONL) is the durable, expensive
        # artifact; the certificate is a separate, free, re-runnable judgment over it.
        # Surfacing the generate command here keeps a finished study from being left
        # un-certified — without a field-study special case in the core shutdown path.
        self._logger.info(
            f'✅ Field Study capture complete → {self._path}\n'
            f'   Next step (no live trade) — generate the acceptance certificate:\n'
            f'   python python/cli/field_study_certificate_cli.py generate '
            f'--latest --release-version <X.Y.Z>'
        )

    def get_path(self) -> Path:
        """Return the JSONL output path."""
        return self._path

    # ============================================
    # Internals
    # ============================================

    def _emit(self, plane: str, event_type: str, **fields: Any) -> None:
        """Build a FieldStudyEvent with the current phase context and write it."""
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
                f'❌ The Field Study capture {self._path} could not be written: {e}. It is '
                f'incomplete from this line on; the session continues.')
