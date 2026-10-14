"""
FiniexTestingIDE - Security Acceptance Study Decision Logic (#358, CORE)

The real-money acceptance test of the account protection: the OrderGuard and the safety circuit
breaker, provoked on a real account, each through the full path a session takes — profile,
session, decision, guard or breaker, venue. It is the sibling of the Live Field Study, which
certifies that orders execute; this one certifies that the rules that stop them FIRE.

One study runs as several stages, each a session with a profile of its own: a soft block stops
every new entry for the rest of its session and the hard stop ends it, so no single session can
provoke everything. Limits are set tiny and orders are the venue's smallest, so a real trip costs
cents; most phases send orders that are MEANT to be refused, and a refusal costs nothing.

What every acceptance study shares lives in AbstractAcceptanceStudy; this class adds how a
submit's size is chosen — at the moment it is sent, from the account and the symbol — and what a
phase's outcome is recorded with.
"""

import math
from typing import Any, Dict, List, Optional

from python.framework.acceptance_studies.abstract_acceptance_study import AbstractAcceptanceStudy
from python.framework.decision_logic.core.security_cert_study.security_cert_phase_machine import (
    SecurityCertPhaseMachine,
)
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.io.artifact_specs import SECURITY_CERT_CAPTURE
from python.framework.types.acceptance_study_types.security_cert_types import (
    LotRule,
    SecurityCertPhase,
    SecurityPhaseType,
)
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseAction,
    PhaseResult,
)
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef
from python.framework.types.trading_env_types.broker_types import SymbolSpecification
from python.framework.types.trading_env_types.order_types import OrderType


class SecurityCertStudy(AbstractAcceptanceStudy):
    """
    Deterministic real-money acceptance test of the account protection (#358).

    Configuration (decision_logic_config): what every acceptance study takes. A phase states its
    own order size as a rule (`lot_rule`), resolved when the order is sent.
    """

    STUDY_LABEL = 'Security Study'
    EVENT_KEY = 'security_study'
    ORDER_COMMENT = 'SecurityStudy'

    def __init__(
        self,
        name: str,
        logger: ScenarioLogger,
        config,
        trading_context: TradingContext = None,
    ):
        super().__init__(name, logger, config, trading_context=trading_context)
        self._phase_by_id: Dict[str, SecurityCertPhase] = {p.phase_id: p for p in self._phases}
        # What each submit went out with — the size a lot rule resolved to at that moment
        self._lots_sent: Dict[str, float] = {}

        self.logger.info(
            f'SecurityCertStudy initialized: {len(self._phases)} phases, '
            f'budget=${self._max_session_cost_usd}'
        )

    # ============================================
    # What this study declares
    # ============================================

    def _parse_phases(self, raw_sequence: List[Dict[str, Any]]) -> List[SecurityCertPhase]:
        return [SecurityCertPhase.from_dict(raw) for raw in raw_sequence]

    @classmethod
    def get_capture_name(cls) -> str:
        return SECURITY_CERT_CAPTURE

    @classmethod
    def get_capture_next_step(cls) -> str:
        return ('the next stage — or, once every stage ran, the certificate:\n'
                '   python python/cli/security_cert_cli.py generate '
                '--latest --release-version <X.Y.Z>')

    @classmethod
    def takes_cold_start_adoption(cls) -> bool:
        # A stage continues what the one before it left: the hard stop's restart stage takes over
        # the holding the hard stop kept. Adoption is what hands it over.
        return True

    # ============================================
    # Factory interface
    # ============================================

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        # Every submit is a market order: the rules it provokes judge an order before or instead
        # of resting it, and a market order is the one that cannot be left behind.
        return [OrderType.MARKET]

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        return cls.get_shared_parameter_schema()

    # ============================================
    # The security study's own behaviour
    # ============================================

    def _build_machine(self) -> SecurityCertPhaseMachine:
        """
        Build the phase machine on first use.

        Returns:
            The machine over this study's phases
        """
        spec = self.trading_api.get_symbol_spec(self._symbol)
        return SecurityCertPhaseMachine(
            self._phases, flat_below_lots=spec.volume_min, base_currency=spec.base_currency)

    def _base_holding(self) -> Optional[float]:
        if self._symbol is None:
            return None
        spec = self.trading_api.get_symbol_spec(self._symbol)
        return self.trading_api.get_asset_balance(spec.base_currency)

    def _resolve_lots(self, action: PhaseAction) -> Optional[float]:
        phase = self._phase_by_id[action.phase_id]
        spec = self.trading_api.get_symbol_spec(self._symbol)
        lots = self._lots_for(phase.lot_rule, spec)
        self._lots_sent[phase.phase_id] = lots
        return lots

    def _phase_result_extra(self, result: PhaseResult) -> Optional[Dict[str, Any]]:
        phase = self._phase_by_id.get(result.phase_id)
        if phase is None or phase.phase_type != SecurityPhaseType.SUBMIT_EXPECT:
            return None
        return {
            'expect': phase.expect.value,
            'expect_reason': phase.expect_reason.value if phase.expect_reason else None,
            'lot_rule': phase.lot_rule.value,
            'lots': self._lots_sent.get(phase.phase_id),
        }

    def _lots_for(self, rule: LotRule, spec: SymbolSpecification) -> float:
        """
        The size a lot rule means now, on the venue's own grid.

        Args:
            rule: How the size is chosen
            spec: The symbol's lot limits and its currencies

        Returns:
            The lots to send
        """
        if rule == LotRule.MIN_LOT:
            return spec.volume_min
        if rule == LotRule.BELOW_MIN:
            # Below the minimum on purpose — the step does not matter to a refusal for size
            return spec.volume_min / 2.0
        if rule == LotRule.BEYOND_FUNDS:
            quote = self.trading_api.get_asset_balance(spec.quote_currency)
            target = 2.0 * quote / self._last_mid
            return min(_on_step(max(target, spec.volume_min), spec), spec.volume_max)
        # BEYOND_HOLDING — a sale of more base than the account holds
        base = self.trading_api.get_asset_balance(spec.base_currency)
        return _on_step(base + spec.volume_min, spec)


def _on_step(lots: float, spec: SymbolSpecification) -> float:
    """
    Round a size UP onto the venue's grid: the minimum plus whole steps.

    Args:
        lots: The size wanted
        spec: The symbol's minimum and step

    Returns:
        The smallest size on the grid that is at least `lots`
    """
    if spec.volume_step <= 0:
        return lots
    steps = math.ceil((lots - spec.volume_min) / spec.volume_step - 1e-9)
    decimals = max(0, -int(math.floor(math.log10(spec.volume_step))))
    return round(spec.volume_min + max(steps, 0) * spec.volume_step, decimals)
