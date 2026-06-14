"""
FiniexTestingIDE - Component Metadata Advisory (#118 Stage 0)

Surfaces a decision logic's author-declared metadata (version + doc link) at run
start and emits a soft, NON-blocking market-fit warning when the run's market type
or instrument falls outside the algo's recommended set. Advisory only — the HARD
market-compatibility check (activity metric, see market_capabilities) handles real
incompatibility; this is the "this algo was not designed for here" nudge.

Shared by both pipelines (sim subprocess startup + AutoTrader session startup).
"""

from typing import Optional

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.scenario_logger import ScenarioLogger


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


def surface_decision_logic_metadata(
    decision_logic: AbstractDecisionLogic,
    broker_type: str,
    symbol: str,
    logger: ScenarioLogger,
) -> None:
    """
    Log the decision logic's metadata and warn on a market/instrument mismatch.

    Args:
        decision_logic: The decision logic for the run
        broker_type: Run broker type (resolves to a market type)
        symbol: Run symbol
        logger: Logger for the version line (INFO) and any warnings (WARNING)
    """
    meta = decision_logic.get_metadata()

    version_line = f"🧬 Algo: {decision_logic.name} v{meta.version}"
    if meta.doc_link:
        version_line += f" — {meta.doc_link}"
    logger.info(version_line)

    if meta.recommended_markets:
        market_type = _resolve_market_type(broker_type)
        if market_type is not None and market_type not in meta.recommended_markets:
            logger.warning(
                f"Market-fit advisory: '{decision_logic.name}' recommends markets "
                f"{list(meta.recommended_markets)} but is running on '{market_type}' "
                f"({broker_type}) — advisory only, not a block."
            )

    if meta.recommended_instruments and symbol not in meta.recommended_instruments:
        logger.warning(
            f"Market-fit advisory: '{decision_logic.name}' recommends instruments "
            f"{list(meta.recommended_instruments)} but is running on '{symbol}' "
            f"— advisory only, not a block."
        )
