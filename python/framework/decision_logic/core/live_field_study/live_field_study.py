"""
FiniexTestingIDE - Live Field Study Decision Logic (#332, CORE)

Operator-driven acceptance-test decision logic: drives the full live pipeline
through a deterministic, wall-clock phase sequence (every order type, modify/cancel
path, rejection battery, partial close, idle heartbeat) against a real broker. It is
the live equivalent of the plan-driven MarginStressProbe.

The phase logic lives in FieldStudyPhaseMachine (a pure state machine); what every
acceptance study shares — the per-tick observation, the dispatch, the session guards and
the capture of the phases — lives in AbstractAcceptanceStudy. This class adds the field
study's own: its order-type families and the capability check over them, the partial
close and the modify of a resting order. Phase 19 ends the session via the existing
request_session_end API (#348).
"""

from typing import Any, Dict, List

from python.framework.acceptance_studies.abstract_acceptance_study import (
    STOP_WORLD_TYPES,
    AbstractAcceptanceStudy,
)
from python.framework.constants.run_layout_constants import FIELD_STUDY_CAPTURE
from python.framework.decision_logic.core.live_field_study.field_study_phase_machine import (
    FieldStudyPhaseMachine,
)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.acceptance_study_types.field_study_types import (
    FieldStudyPhase,
    PhaseType,
)
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseAction,
    PhaseActionKind,
)
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
)
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef
from python.framework.types.trading_env_types.order_types import OrderType

# Limit-family phases require broker LIMIT support — auto-skipped on adapters without it.
_LIMIT_FAMILY = frozenset({
    PhaseType.LIMIT_OPEN,
    PhaseType.LIMIT_MODIFY,
    PhaseType.LIMIT_CANCEL,
    PhaseType.MULTI_LIMIT,
    PhaseType.MULTI_CANCEL,
})

# Stop-family phases require broker STOP support — auto-skipped without it. Separate from
# the limit family on purpose: a venue or a pipeline can carry one and not the other, and
# for most of this project's life the live path carried limits and refused stops (#500).
_STOP_FAMILY = frozenset({
    PhaseType.STOP_CANCEL,
})


class LiveFieldStudy(AbstractAcceptanceStudy):
    """
    Deterministic live acceptance-test decision logic (#332).

    Configuration (decision_logic_config), beyond what every acceptance study takes:
        lot_size: Default order size when a phase omits its own
        limit_offset_pct: Default resting-limit distance from market (fraction)
        max_rearm_attempts: Re-arm cap for LIMIT_OPEN phases
        rearm_offset_pct: Fraction the limit offset shrinks per re-arm (toward market)
    """

    STUDY_LABEL = 'Field Study'
    EVENT_KEY = 'field_study'
    ORDER_COMMENT = 'FieldStudy'

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None,
    ):
        super().__init__(name, logger, config, trading_context=trading_context)
        self._default_lot = self.params.get('lot_size')
        self._limit_offset_pct = self.params.get('limit_offset_pct')
        self._max_rearm_attempts = self.params.get('max_rearm_attempts')
        self._rearm_offset_pct = self.params.get('rearm_offset_pct')

        self.logger.info(
            f'LiveFieldStudy initialized: {len(self._phases)} phases, '
            f'lot_size={self._default_lot}, limit_offset={self._limit_offset_pct}, '
            f'max_rearm={self._max_rearm_attempts}, '
            f'budget=${self._max_session_cost_usd}'
        )

    # ============================================
    # What this study declares
    # ============================================

    def _parse_phases(self, raw_sequence: List[Dict[str, Any]]) -> List[FieldStudyPhase]:
        return [FieldStudyPhase.from_dict(raw) for raw in raw_sequence]

    @classmethod
    def get_capture_name(cls) -> str:
        return FIELD_STUDY_CAPTURE

    @classmethod
    def get_capture_next_step(cls) -> str:
        return ('generate the acceptance certificate:\n'
                '   python python/cli/field_study_certificate_cli.py generate '
                '--latest --release-version <X.Y.Z>')

    @classmethod
    def takes_cold_start_adoption(cls) -> bool:
        # It funds both sides on purpose and asserts its own flat order book (#332), so
        # adoption would claim its funding.
        return False

    # ============================================
    # Factory interface
    # ============================================

    def on_cold_start(self, situation: ColdStartSituation) -> ColdStartVerdict:
        """
        Programmed boot reaction (#493): unreachable by construction, and written anyway.

        The Field Study is excluded from cold-start adoption: it funds both sides on purpose
        and asserts its own flat order book at boot (#332), so the boot step skips adoption
        entirely when it owns the session. This hook can therefore never fire.

        It exists because the contract is enforced on the DECLARATION, not on the pipeline —
        this logic declares a resting LIMIT type. Writing the answer down is cheaper than an
        exemption, and if the exclusion is ever lifted the answer is already here rather than
        a hole nobody notices.

        Args:
            situation: What the boot found at the venue

        Returns:
            A declining verdict with the reason
        """
        return ColdStartVerdict(
            False,
            'the Field Study asserts a flat book at boot (#332) — adoption is skipped for it, '
            'so this answer should never be read',
        )

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        # MARKET is always needed; LIMIT only if the configured phase sequence uses a
        # limit phase — so a limit-free sequence (e.g. the mock session) can run
        # against a MARKET-only adapter.
        types = [OrderType.MARKET]
        for raw in decision_logic_config.get('phase_sequence', []):
            try:
                phase_type = PhaseType(raw.get('phase_type'))
            except ValueError:
                continue
            if phase_type in _LIMIT_FAMILY and OrderType.LIMIT not in types:
                types.append(OrderType.LIMIT)
            if phase_type in _STOP_FAMILY and OrderType.STOP not in types:
                types.append(OrderType.STOP)
        return types

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return {
            **cls.get_shared_parameter_schema(),
            'lot_size': InputParamDef(
                param_type=float, default=0.01, min_val=0.0,
                description='Default order size (min-lot for the Field Study)',
            ),
            'limit_offset_pct': InputParamDef(
                param_type=float, default=0.002, min_val=0.0, max_val=1.0,
                description='Default resting-limit distance from market (fraction)',
            ),
            'max_rearm_attempts': InputParamDef(
                param_type=int, default=6, min_val=0,
                description='Re-arm cap for LIMIT_OPEN phases (0 = unbounded: re-arm until fill / budget / session timeout)',
            ),
            'rearm_offset_pct': InputParamDef(
                param_type=float, default=0.25, min_val=0.0, max_val=1.0,
                description='Fraction the limit offset shrinks per re-arm',
            ),
        }

    # ============================================
    # The field study's own actions
    # ============================================

    def _dispatch_study_action(self, action: PhaseAction) -> None:
        if action.kind == PhaseActionKind.CLOSE_PARTIAL:
            self._close_partial(action)
        elif action.kind == PhaseActionKind.MODIFY_LIMIT:
            self._modify_resting(action)

    def _close_partial(self, action: PhaseAction) -> None:
        positions = self.trading_api.get_open_positions(self._symbol)
        if not positions:
            return
        pos = positions[0]
        if self.trading_api.is_pending_close(pos.position_id):
            return
        fraction = action.close_fraction if action.close_fraction is not None else 0.5
        self.trading_api.close_position(pos.position_id, lots=pos.lots * fraction)

    def _modify_resting(self, action: PhaseAction) -> None:
        if not self._phase_order_ids or action.price is None:
            return
        order_id = self._phase_order_ids[-1]
        # Same split as the shared cancel: a stop's price is its TRIGGER and it is amended
        # through its own path, which reaches Kraken's `trigger_price` rather than
        # `limit_price` (#500).
        target = next((o for o in self.trading_api.get_active_orders()
                       if o.pending_order_id == order_id), None)
        if target is not None and target.order_type in STOP_WORLD_TYPES:
            self.trading_api.modify_stop_order(
                order_id=order_id, new_stop_price=action.price)
            return
        self.trading_api.modify_limit_order(order_id=order_id, price=action.price)

    # ============================================
    # Internal helpers
    # ============================================

    def _build_machine(self) -> FieldStudyPhaseMachine:
        """
        Build the phase machine on first use, after capability filtering.

        Returns:
            The machine over the phases this broker can carry
        """
        caps = self.trading_api.get_order_capabilities()
        unsupported = {
            OrderType.LIMIT: _LIMIT_FAMILY,
            OrderType.STOP: _STOP_FAMILY,
        }
        for order_type, family in unsupported.items():
            if caps.supports_order_type(order_type):
                continue
            for phase in self._phases:
                if phase.phase_type in family:
                    phase.enabled = False
                    self.logger.info(
                        f"Field Study phase '{phase.phase_id}' auto-skipped "
                        f'(broker lacks {order_type.value.upper()} support)'
                    )

        return FieldStudyPhaseMachine(
            phases=self._phases,
            default_lot=self._default_lot,
            default_limit_offset_pct=self._limit_offset_pct,
            max_rearm_attempts=self._max_rearm_attempts,
            rearm_shrink_pct=self._rearm_offset_pct,
        )
