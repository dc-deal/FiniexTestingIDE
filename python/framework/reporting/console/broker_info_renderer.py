"""
FiniexTestingIDE - Broker Information Renderer
Formats static broker configuration data for logs and reports
"""

from typing import Optional
from python.framework.types.trading_env_types.broker_types import BrokerSpecification, SymbolSpecification


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
