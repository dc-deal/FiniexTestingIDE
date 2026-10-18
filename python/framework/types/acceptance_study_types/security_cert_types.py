"""
FiniexTestingIDE - Security Acceptance Study Types (#358)

The security study's own phase types: what its phases do, how an order's size is chosen, and what
a phase expects to come of its order. The study certifies the account protection — the OrderGuard
and the safety circuit breaker — on a real account, so most of its orders are MEANT to be refused,
and a phase names the outcome it waits for, with the reason where it expects a refusal.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Dict, Optional

from python.framework.types.acceptance_study_types.study_phase_types import PhaseSide, StudyPhase
from python.framework.types.trading_env_types.order_types import RejectionReason


class SecurityPhaseType(StrEnum):
    """The kind of step a security phase takes."""
    START_FLAT = 'start_flat'                # the account holds none of the symbol's base
    SUBMIT_EXPECT = 'submit_expect'          # send one market order and expect one outcome
    WAIT = 'wait'                            # let time pass — a cooldown runs out, a tick arrives
    AWAIT_SESSION_END = 'await_session_end'  # the hard stop is expected to end the session
    FORCE_CLOSE = 'force_close'              # close what is open, cancel what rests
    FINAL_SUMMARY = 'final_summary'          # ask for the session to end


class LotRule(StrEnum):
    """How a submit's size is chosen — at the moment it is sent, from the account and the symbol."""
    MIN_LOT = 'min_lot'                # the venue's smallest order
    BELOW_MIN = 'below_min'            # half of it — refused as an invalid lot, nothing sent
    BEYOND_FUNDS = 'beyond_funds'      # twice what the quote balance buys — refused for funds
    BEYOND_HOLDING = 'beyond_holding'  # a sale of more base than the account holds — refused for funds


class ExpectedOutcome(StrEnum):
    """What a submit is expected to come to."""
    FILLED = 'filled'            # it trades
    DENIED = 'denied'            # refused here, before anything was sent — with its reason
    SUPPRESSED = 'suppressed'    # the circuit breaker blocked new entries; no order at all


@dataclass
class SecurityCertPhase(StudyPhase):
    """
    One phase of the security study (typed view of a config dict).

    Args:
        phase_id: Stable identifier (e.g. 'g_cooled_buy')
        phase_type: The step kind
        enabled: Skip the phase when False
        fill_timeout_s: Wall-clock seconds to wait for the outcome before the phase fails
        side: Trade side (LONG buys, SHORT sells at spot)
        lot_rule: How the submit's size is chosen
        expect: The outcome a SUBMIT_EXPECT phase waits for
        expect_reason: The refusal's reason a DENIED expectation names
        wait_s: How long a WAIT phase lets pass
    """
    phase_type: SecurityPhaseType
    lot_rule: LotRule = LotRule.MIN_LOT
    expect: Optional[ExpectedOutcome] = None
    expect_reason: Optional[RejectionReason] = None
    wait_s: float = 0.0

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> 'SecurityCertPhase':
        """
        Build a SecurityCertPhase from a raw config dict.

        Args:
            raw: Phase spec from the profile's phase_sequence

        Returns:
            Typed phase with defaults applied
        """
        phase = cls(
            phase_id=raw['phase_id'],
            phase_type=SecurityPhaseType(raw['phase_type']),
            side=PhaseSide(raw.get('side', PhaseSide.NONE.value)),
            enabled=bool(raw.get('enabled', True)),
            fill_timeout_s=float(raw.get('fill_timeout_s', 30.0)),
            lot_rule=LotRule(raw.get('lot_rule', LotRule.MIN_LOT.value)),
            expect=ExpectedOutcome(raw['expect']) if raw.get('expect') else None,
            expect_reason=(RejectionReason(raw['expect_reason'])
                           if raw.get('expect_reason') else None),
            wait_s=float(raw.get('wait_s', 0.0)),
        )
        if phase.phase_type == SecurityPhaseType.SUBMIT_EXPECT:
            # A submit with no expectation certifies nothing, and a denial with no reason
            # cannot tell an invalid lot from a cooldown — refused when the profile loads.
            if phase.expect is None:
                raise ValueError(f"security phase '{phase.phase_id}': a submit names `expect`")
            if (phase.expect == ExpectedOutcome.DENIED) != (phase.expect_reason is not None):
                raise ValueError(
                    f"security phase '{phase.phase_id}': `expect_reason` goes with "
                    f"`expect: denied`, and only with it")
            if phase.side == PhaseSide.NONE:
                raise ValueError(f"security phase '{phase.phase_id}': a submit names its `side`")
        return phase


class SecurityStageId(StrEnum):
    """The stages of one security study, in the order they run — each a session of its own."""
    GUARDS = 'guards'              # the order guard: refusals, the cooldown, its reset
    SOFT = 'soft'                  # the soft circuit breaker on the real account value
    RESTART = 'restart'            # the baseline survives a restart
    HARD_KEPT = 'hard_kept'        # the hard stop at spot, the holding kept
    HARD_RESTART = 'hard_restart'  # a restart after the hard stop, the holding sold


@dataclass(frozen=True)
class SecurityStage:
    """
    One stage of the security study.

    Args:
        stage_id: Which stage
        profile: Its profile's file name in the security study's profile folder
    """
    stage_id: SecurityStageId
    profile: str


class RuleCertification(StrEnum):
    """How the security certificate stands behind one rule."""
    LIVE = 'live'          # provoked on real money in one of the stages
    OFFLINE = 'offline'    # proven by a named test of the same commit — a real account cannot
                           # provoke it safely (a recovery, a peak, a day boundary, a lost answer)
    MARGIN = 'margin'      # a margin path; waits for the first margin adapter (#209)


class SecurityRule(StrEnum):
    """Every rule of the account protection the certificate answers for."""
    INVALID_LOT_REFUSED = 'invalid_lot_refused'
    INVALID_LOT_DOES_NOT_ARM = 'invalid_lot_does_not_arm'
    FUNDS_REFUSED_ON_A_BUY = 'funds_refused_on_a_buy'
    FUNDS_REFUSED_ON_A_SALE_WITHOUT_BASE = 'funds_refused_on_a_sale_without_base'
    COOLDOWN_ARMS = 'cooldown_arms'
    COOLDOWN_HOLDS_ONE_DIRECTION = 'cooldown_holds_one_direction'
    COOLDOWN_RUNS_OUT = 'cooldown_runs_out'
    COOLDOWN_COUNT_DOES_NOT_DECAY = 'cooldown_count_does_not_decay'
    A_FILL_RESETS_THE_COUNT = 'a_fill_resets_the_count'
    VENUE_REFUSAL_ARMS = 'venue_refusal_arms'
    PROTECTIVE_AMEND_REFUSAL_DOES_NOT_ARM = 'protective_amend_refusal_does_not_arm'
    SOFT_BLOCK_ON_THE_ACCOUNT_VALUE = 'soft_block_on_the_account_value'
    SOFT_BLOCK_NAMES_EVERY_LIMIT = 'soft_block_names_every_limit'
    CLOSING_STAYS_ALLOWED = 'closing_stays_allowed'
    SOFT_BLOCK_CLEARS = 'soft_block_clears'
    MIN_EQUITY_FLOOR = 'min_equity_floor'
    HIGH_WATER_MARK_TRAILS = 'high_water_mark_trails'
    DAILY_BASELINE_RESETS = 'daily_baseline_resets'
    BASELINE_SURVIVES_RESTART = 'baseline_survives_restart'
    DAILY_BASELINE_STARTS_FRESH = 'daily_baseline_starts_fresh'
    HARD_STOP_KEEPS_A_SPOT_HOLDING = 'hard_stop_keeps_a_spot_holding'
    HARD_STOP_FIRES_AGAIN_AFTER_RESTART = 'hard_stop_fires_again_after_restart'
    HARD_STOP_SELLS_WHEN_ALLOWED = 'hard_stop_sells_when_allowed'
    HARD_STOP_NAMES_AN_UNCONFIRMED_CLOSE = 'hard_stop_names_an_unconfirmed_close'
    MARGIN_EMERGENCY_FLATTEN = 'margin_emergency_flatten'
    MARGIN_BALANCE_FLOOR = 'margin_balance_floor'
