"""
FiniexTestingIDE - Broker Summary
Displays broker configuration in batch summary reports
"""

from python.framework.reporting.console.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import BrokerInfoRow, BrokerReport
from python.framework.utils.console_renderer import ConsoleRenderer


class BrokerSummary(AbstractBatchSummarySection):
    """Renders the broker-configuration section from the unified model (#391)."""

    _section_title = '🏦 BROKER CONFIGURATION'

    def __init__(self, broker_report: BrokerReport):
        """
        Initialize broker summary.

        Args:
            broker_report: Unified broker-configuration report (one unit per broker)
        """
        self._units = broker_report.units

    def render(self, renderer: ConsoleRenderer, compact: bool = False, threshold: int = 9):
        """
        Render broker summary section.

        Args:
            renderer: Console renderer for formatting
            compact: If True, collapse scenario lists above threshold to count
            threshold: Max scenarios to list before collapsing
        """
        self._render_section_header(renderer)

        if not self._units:
            print(renderer.red("⚠️  No broker configuration available"))
            return

        for i, unit in enumerate(self._units):
            # Separator between broker blocks
            if i > 0:
                print(
                    "   ───────────────────────────────────────────────────────────────────────────")
                print("")

            self._render_broker_block(unit, compact=compact, threshold=threshold)

            # Traded symbols table
            print("")
            renderer.print_bold("   TRADED SYMBOLS")
            print("")
            self._render_symbols_table(unit)

    def _render_broker_block(self, unit: BrokerInfoRow, compact: bool, threshold: int):
        """Render one broker's static configuration and scenario list."""
        indent = "   "
        print(f"{indent}Market:  {unit.market_type}")
        print(f"{indent}Company: {unit.company}")
        print(f"{indent}Server: {unit.server} | Mode: {unit.trade_mode.upper()}")
        print(f"{indent}Leverage: 1:{unit.leverage} | Margin: {unit.margin_mode}")
        print(f"{indent}Risk: MC {unit.margin_call_level}% / SO {unit.stopout_level}% | "
              f"Hedging: {'✅' if unit.hedging_allowed else '❌'}")
        if unit.config_hash:
            print(f"{indent}Config:  [{unit.config_hash}]")

        # Scenarios as bullet points (collapsed to count in compact mode)
        if unit.scenarios:
            if compact and len(unit.scenarios) > threshold:
                print(f"{indent}Scenarios: {len(unit.scenarios)} scenarios — see log for full list")
            else:
                print(f"{indent}Scenarios:")
                for scenario in unit.scenarios:
                    print(f"{indent}  • {scenario}")

    def _render_symbols_table(self, unit: BrokerInfoRow):
        """Render the traded-symbols table for one broker."""
        indent = "   "
        if not unit.symbols:
            print(f"{indent}No symbols available")
            return

        # Header
        print(f"{indent}Symbol    | Lots        | Contract | Tick     | Currencies | Swap L/S")
        print(f"{indent}" + "-" * 75)

        # Rows
        for sym in unit.symbols:
            lot_range = f"{sym.volume_min}-{sym.volume_max}"
            currencies = f"{sym.base_currency}/{sym.quote_currency}"
            swap = f"{sym.swap_long}/{sym.swap_short}"
            print(
                f"{indent}{sym.symbol:<10}| {lot_range:<12}| {sym.contract_size:<9,}| "
                f"{sym.tick_size:<9}| {currencies:<11}| {swap}")
