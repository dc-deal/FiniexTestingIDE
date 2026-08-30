"""
FiniexTestingIDE - Component Metadata Advisory (#118 Stage 0)

Two different things about a component's author-declared metadata, deliberately kept apart:

- `surface_decision_logic_version` LOGS the version + doc link into the run's own log. That is
  an OBSERVATION — it puts a fact where a reader of that log needs it, and nothing judges it.
- `check_market_fit` RETURNS findings when the run's market type or instrument falls outside the
  component's recommended set. That is a JUDGEMENT, so it travels as a `ValidationFinding` into a
  validation channel (Tier 1) and never through the log pot. Both halves used to be one
  `logger.warning`, which made the report classify a validator's verdict as "an observation
  nobody adjudicated" — see docs/architecture/warnings_errors_tiers.md.

Advisory only — the HARD market-compatibility check (activity metric, see market_capabilities)
handles real incompatibility; this is the "this algo was not designed for here" nudge.

Both functions take metadata, never a component instance: `get_metadata()` is a `@classmethod`,
so the sim batch resolves the class (`DecisionLogicFactory.resolve_logic_class`) and answers the
question BEFORE any subprocess starts. Every input is static — the declared metadata, the broker
type, the symbol — so nothing about market fit was ever a runtime question.
"""

from typing import List, Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.component_metadata_types import ComponentMetadata
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)

# Stable id of the assertion these findings come from.
_MARKET_FIT_CHECK = 'market_fit'


def _resolve_market_type(broker_type) -> Optional[str]:
    """
    Best-effort broker_type → market-type string; never raises.

    The two pipelines pass broker_type differently (AutoTrader: the broker string;
    sim: a BrokerType enum). Try the value and its `.value`; on any failure return
    None so the advisory is skipped — it must never crash a run.

    Args:
        broker_type: Broker identifier (string or BrokerType enum)

    Returns:
        Market-type string (e.g. 'forex'), or None if it cannot be resolved
    """
    mcm = MarketConfigManager()
    for candidate in (broker_type, getattr(broker_type, 'value', None)):
        if candidate is None:
            continue
        try:
            return mcm.get_market_type(candidate).value
        except Exception:
            continue
    return None


def surface_decision_logic_version(
    decision_logic: AbstractDecisionLogic,
    logger: AbstractLogger,
) -> None:
    """
    Log the decision logic's version and doc link into the run's own log.

    Args:
        decision_logic: The decision logic for the run
        logger: Logger for the version line (INFO)
    """
    meta = decision_logic.get_metadata()
    version_line = f'🧬 Algo: {decision_logic.name} v{meta.version}'
    if meta.doc_link:
        version_line += f' — {meta.doc_link}'
    logger.info(version_line)


def check_market_fit(
    meta: ComponentMetadata,
    component_name: str,
    broker_type,
    symbol: str,
    scope: str,
) -> List[ValidationFinding]:
    """
    Advisory findings when a component is running outside its recommended market or instrument.

    Args:
        meta: The component's declared metadata
        component_name: Name to show in the message (the algo / worker name)
        broker_type: Run broker type (string or BrokerType enum; resolves to a market type)
        symbol: Run symbol
        scope: The unit the finding concerns (scenario name / profile name)

    Returns:
        One finding per mismatch — empty when the component declares no recommendation, or fits
    """
    findings = []

    if meta.recommended_markets:
        market_type = _resolve_market_type(broker_type)
        if market_type is not None and market_type not in meta.recommended_markets:
            findings.append(_finding(
                f"Market-fit advisory: '{component_name}' recommends markets "
                f"{list(meta.recommended_markets)} but is running on '{market_type}' "
                f"({broker_type}) — advisory only, not a block.", scope))

    if meta.recommended_instruments and symbol not in meta.recommended_instruments:
        findings.append(_finding(
            f"Market-fit advisory: '{component_name}' recommends instruments "
            f"{list(meta.recommended_instruments)} but is running on '{symbol}' "
            f"— advisory only, not a block.", scope))

    return findings


def _finding(message: str, scope: str) -> ValidationFinding:
    """
    Build one market-fit advisory finding.

    Args:
        message: Operator-readable text
        scope: The unit the finding concerns

    Returns:
        The advisory ValidationFinding
    """
    return ValidationFinding(
        severity=Severity.WARNING, check=_MARKET_FIT_CHECK, domain=ValidationDomain.ALGO,
        message=message, scope=scope)
