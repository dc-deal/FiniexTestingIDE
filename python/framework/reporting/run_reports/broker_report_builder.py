"""
Broker report builder (#391) — the broker-configuration postprocessor.

Maps a resolved `BrokerConfig` (static broker spec + per-symbol specs) to a `BrokerReport`.
Two entry points, one shared row mapper:
- sim batch → one `BrokerInfoRow` per broker (from `broker_scenario_map`: scenarios + symbols);
- live session → one `BrokerInfoRow` for the session's single broker + symbol (no scenario grid).

Both read the **already-resolved** `BrokerConfig` (its `broker_type` is a `BrokerType` enum),
never a raw config key — so the sim `data_broker_type` vs. live `broker_type` JSON-key asymmetry
is irrelevant here: it is resolved upstream (sim: `BrokerDataPreparator`; live: AutoTrader
startup) before the report is built. Reads the batch/config directly — NOT via `RunUnit`, because
this is a config snapshot keyed by broker, not per-unit (same pattern as scenario_details).
"""

from typing import List

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.types.api.report_types import (
    BrokerInfoRow, BrokerReport, BrokerSymbolRow)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.trading_env_types.broker_types import SymbolSpecification


def build_broker_report_from_batch(batch: BatchExecutionSummary) -> BrokerReport:
    """
    Build the broker report from a sim batch — one row per broker.

    Args:
        batch: The completed batch summary (carries broker_scenario_map)

    Returns:
        BrokerReport with one BrokerInfoRow per broker
    """
    market_config = MarketConfigManager()
    units = [
        _to_broker_row(info.broker_config, list(info.scenarios), sorted(info.symbols), market_config)
        for info in batch.broker_scenario_map.values()
    ]
    return BrokerReport(units=units)


def build_broker_report_from_session(broker_config: BrokerConfig, symbol: str) -> BrokerReport:
    """
    Build the broker report for a live session — the single broker + traded symbol.

    Args:
        broker_config: The resolved live BrokerConfig (the executor's broker)
        symbol: The session's traded symbol

    Returns:
        BrokerReport with one BrokerInfoRow (no scenario grid for live)
    """
    return BrokerReport(units=[
        _to_broker_row(broker_config, [], [symbol], MarketConfigManager())])


def _to_broker_row(
    broker_config: BrokerConfig,
    scenarios: List[str],
    symbols: List[str],
    market_config: MarketConfigManager,
) -> BrokerInfoRow:
    """Map a resolved BrokerConfig (+ its scenarios/symbols) to a BrokerInfoRow."""
    spec = broker_config.get_broker_specification()
    broker_type = broker_config.broker_type.value
    return BrokerInfoRow(
        broker_type=broker_type,
        market_type=market_config.get_market_type(broker_type).value,
        company=spec.company,
        server=spec.server,
        trade_mode=spec.trade_mode,
        leverage=spec.leverage,
        margin_mode=spec.margin_mode.value,
        margin_call_level=spec.margin_call_level,
        stopout_level=spec.stopout_level,
        hedging_allowed=spec.hedging_allowed,
        config_hash=broker_config.config_hash or '',
        scenarios=scenarios,
        symbols=[_to_symbol_row(broker_config.get_symbol_specification(s)) for s in symbols],
    )


def _to_symbol_row(spec: SymbolSpecification) -> BrokerSymbolRow:
    """Map a SymbolSpecification to a BrokerSymbolRow."""
    return BrokerSymbolRow(
        symbol=spec.symbol,
        volume_min=spec.volume_min,
        volume_max=spec.volume_max,
        volume_step=spec.volume_step,
        contract_size=spec.contract_size,
        tick_size=spec.tick_size,
        base_currency=spec.base_currency,
        quote_currency=spec.quote_currency,
        swap_long=spec.swap_long,
        swap_short=spec.swap_short,
    )
