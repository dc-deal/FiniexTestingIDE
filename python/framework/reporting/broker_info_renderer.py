"""
FiniexTestingIDE - Broker Information Renderer
Formats static broker configuration data for logs and reports
"""

from typing import Dict, Optional, List
from python.framework.types.broker_types import BrokerSpecification, SymbolSpecification
from python.framework.types.market_config_types import MarketType


class BrokerInfoRenderer:
    """Renders broker configuration in various formats for logs and reports."""

    @staticmethod
    def render_detailed(
        broker_spec: BrokerSpecification,
        symbol_spec: Optional[SymbolSpecification] = None,
        indent: str = ""
    ) -> str:
        """
        Render detailed broker info for scenario logs.

        Args:
            broker_spec: Broker specification
            symbol_spec: Optional symbol specification
            indent: Line indentation prefix

        Returns:
            Multi-line formatted string
        """
        lines = [
            f"{indent}🏦 BROKER CONFIGURATION",
            f"{indent}   Company: {broker_spec.company}",
            f"{indent}   Server: {broker_spec.server} | Mode: {broker_spec.trade_mode.upper()}",
            f"{indent}   Leverage: 1:{broker_spec.leverage} | Margin: {broker_spec.margin_mode.value}",
            f"{indent}   Risk Management: MC {broker_spec.margin_call_level}% / SO {broker_spec.stopout_level}%",
            f"{indent}   Hedging: {'✅ Allowed' if broker_spec.hedging_allowed else '❌ Disabled'}",
        ]

        if symbol_spec:
            lines.extend([
                f"{indent}📊 SYMBOL: {symbol_spec.symbol}",
                f"{indent}   Description: {symbol_spec.description}",
                f"{indent}   Lot Range: {symbol_spec.volume_min}-{symbol_spec.volume_max} (step: {symbol_spec.volume_step})",
                f"{indent}   Contract: {symbol_spec.contract_size:,} | Tick: {symbol_spec.tick_size}",
                f"{indent}   Currencies: {symbol_spec.base_currency}/{symbol_spec.quote_currency} (margin: {symbol_spec.margin_currency})",
                f"{indent}   Swap: Long {symbol_spec.swap_long} / Short {symbol_spec.swap_short} ({symbol_spec.swap_mode.value})",
            ])

        return "\n".join(lines)

    @staticmethod
    def render_single_line(
        broker_spec: BrokerSpecification
    ) -> str:
        """
        Render single-line broker info for global logs.

        Args:
            broker_spec: Broker specification
            symbol: Optional symbol name

        Returns:
            Single-line formatted string
        """
        hedging = "✅" if broker_spec.hedging_allowed else "❌"

        parts = [
            f"🏦 Broker: {broker_spec.company}",
            f"({broker_spec.trade_mode})",
            f"Leverage: 1:{broker_spec.leverage}",
            f"MC:{broker_spec.margin_call_level}%",
            f"SO:{broker_spec.stopout_level}%",
            f"Hedging: {hedging}",
        ]

        return " | ".join(parts)

    @staticmethod
    def render_summary_table(
        broker_spec: BrokerSpecification = None,
        scenarios: List[str] = None,
        indent: str = "   ",
        market_type=MarketType,
    ) -> str:
        """
        Render broker info as table for batch summaries.

        Args:
            broker_spec: BrokerSpecification object
            scenarios: List of scenario names
            indent: Line indentation

        Returns:
            Multi-line table string
        """
        if not broker_spec:
            return f"{indent}No broker data available"

        lines = [
            f"{indent}Market:  {market_type.value}",
            f"{indent}Company: {broker_spec.company}",
            f"{indent}Server: {broker_spec.server} | Mode: {broker_spec.trade_mode.upper()}",
            f"{indent}Leverage: 1:{broker_spec.leverage} | Margin: {broker_spec.margin_mode.value}",
            f"{indent}Risk: MC {broker_spec.margin_call_level}% / SO {broker_spec.stopout_level}% | Hedging: {'✅' if broker_spec.hedging_allowed else '❌'}",
        ]

        # Add scenarios as bullet points
        if scenarios:
            lines.append(f"{indent}Scenarios:")
            for scenario in scenarios:
                lines.append(f"{indent}  • {scenario}")

        return "\n".join(lines)

    @staticmethod
    def render_symbols_table(
        symbol_specs:  Dict[str, SymbolSpecification],
        indent: str = "   "
    ) -> str:
        """
        Render symbols table for batch summaries.

        Args:
            symbol_specs: List of symbol specifications
            indent: Line indentation

        Returns:
            Multi-line table string
        """
        if not symbol_specs:
            return f"{indent}No symbols available"

        # Header
        lines = [
            f"{indent}Symbol    | Lots        | Contract | Tick     | Currencies | Swap L/S"
        ]
        lines.append(f"{indent}" + "-" * 75)

        # Rows
        for symbol, spec in symbol_specs.items():
            lot_range = f"{spec.volume_min}-{spec.volume_max}"
            currencies = f"{spec.base_currency}/{spec.quote_currency}"
            swap = f"{spec.swap_long}/{spec.swap_short}"

            lines.append(
                f"{indent}{symbol:<10}| {lot_range:<12}| {spec.contract_size:<9,}| {spec.tick_size:<9}| {currencies:<11}| {swap}"
            )

        return "\n".join(lines)
