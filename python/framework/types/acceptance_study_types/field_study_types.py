"""
FiniexTestingIDE - Live Field Study Types (#332)

The field study's own phase types: which operations its phases exercise, and the typed view of
one phase as the profile declares it. Phases are declared as decision_logic_config (parsed from a
list of dicts); the protocol they share with every acceptance study — actions, outcomes, the
per-tick observation — lives in study_phase_types.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Dict, Optional

from python.framework.types.acceptance_study_types.study_phase_types import PhaseSide, StudyPhase


class PhaseType(StrEnum):
    """The kind of operation a phase exercises."""
    MARKET_OPEN = 'market_open'
    MARKET_CLOSE_ALL = 'market_close_all'
    LIMIT_OPEN = 'limit_open'
    LIMIT_MODIFY = 'limit_modify'
    LIMIT_CANCEL = 'limit_cancel'
    STOP_CANCEL = 'stop_cancel'
    # #503 — open a position WITH a declared level, confirm the venue is holding the
    # protective order the framework placed for it, then close and confirm it is gone.
    PROTECTIVE_LEVEL = 'protective_level'
    MULTI_LIMIT = 'multi_limit'
    MULTI_CANCEL = 'multi_cancel'
    PARTIAL_CLOSE = 'partial_close'
    IDLE = 'idle'
    FORCE_CLOSE = 'force_close'
    FINAL_SUMMARY = 'final_summary'


@dataclass
class FieldStudyPhase(StudyPhase):
    """
    One phase of the Field Study sequence (typed view of a config dict).

    Args:
        phase_id: Stable identifier (e.g. 'market_long_open')
        phase_type: The operation kind
        enabled: Skip the phase when False
        fill_timeout_s: Wall-clock seconds to wait for a fill before timeout/re-arm/skip
        side: Trade side (LONG/SHORT/NONE)
        expect_rejection: The submit is expected to be rejected (spot SHORT, sub-min lot)
        lots: Order size (None → use the decision logic's default lot_size)
        limit_offset_pct: How far the resting limit sits from the market (fraction, e.g. 0.002)
        stop_loss_offset_pct: How far below the market the PROTECTIVE_LEVEL phase's
            declared stop sits (fraction, e.g. 0.05). Wide on purpose: the phase proves
            the order RESTS at the venue, and a stop that triggers inside it would prove
            the offset was too tight rather than anything about the feature (#503)
        order_count: Number of orders for MULTI_LIMIT
        post_wait_s: Settle window after a fill / close before advancing
        idle_seconds: Quiet-period duration for IDLE phases
        rearm: Re-arm-until-fill for LIMIT_OPEN (re-price toward market on timeout)
    """
    phase_type: PhaseType
    expect_rejection: bool = False
    lots: Optional[float] = None
    limit_offset_pct: Optional[float] = None
    stop_loss_offset_pct: Optional[float] = None
    order_count: int = 1
    post_wait_s: float = 2.0
    idle_seconds: float = 20.0
    rearm: bool = False

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> 'FieldStudyPhase':
        """
        Build a FieldStudyPhase from a raw config dict.

        Args:
            raw: Phase spec from the profile's phase_sequence

        Returns:
            Typed FieldStudyPhase with defaults applied
        """
        return cls(
            phase_id=raw['phase_id'],
            phase_type=PhaseType(raw['phase_type']),
            side=PhaseSide(raw.get('side', PhaseSide.NONE.value)),
            enabled=bool(raw.get('enabled', True)),
            expect_rejection=bool(raw.get('expect_rejection', False)),
            lots=raw.get('lots'),
            limit_offset_pct=raw.get('limit_offset_pct'),
            stop_loss_offset_pct=raw.get('stop_loss_offset_pct'),
            order_count=int(raw.get('order_count', 1)),
            fill_timeout_s=float(raw.get('fill_timeout_s', 30.0)),
            post_wait_s=float(raw.get('post_wait_s', 2.0)),
            idle_seconds=float(raw.get('idle_seconds', 20.0)),
            rearm=bool(raw.get('rearm', False)),
        )
