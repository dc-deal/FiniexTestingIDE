"""
FiniexTestingIDE - Broker Summary
Displays broker configuration in batch summary reports
"""

from typing import Dict, List, Set
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.batch_reporting.broker_info_renderer import BrokerInfoRenderer
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.broker_types import BrokerSpecification, SymbolSpecification
from python.configuration.app_config_manager import AppConfigManager


class BrokerSummary:
    """Renders broker configuration summary for batch reports."""

    def __init__(
        self,
        batch_summary: BatchExecutionSummary,
        app_config: AppConfigManager
    ):
        """
        Initialize broker summary.

        Args:
            batch_summary: Batch execution results
            app_config: Application configuration
        """
        self.batch_summary = batch_summary
        self.app_config = app_config

        # Collect broker data
        self._broker_scenario_map = batch_summary.broker_scenario_map
        self._broker_spec: Dict[str, BrokerSpecification] = {}
        self._broker_scenario_list: Dict[str, List[str]] = {}
        self._symbol_specs: Dict[str, Dict[str, SymbolSpecification]] = {}
        self._collect_broker_data()

    def _collect_broker_data(self):
        """Collect broker configuration and symbols from scenarios."""
        for broker_key, broker_scenario_info in self._broker_scenario_map.items():
            broker_config = broker_scenario_info.broker_config
            spec = broker_scenario_info.broker_config.get_broker_specification()
            # Collect symbol specs
            self._broker_spec[broker_key] = spec
            self._broker_scenario_list[broker_key] = broker_scenario_info.scenarios
            # already pre sorted and de-duplicated.
            self._symbol_specs[broker_key] = {}
            for symbol in sorted(broker_scenario_info.symbols):
                symbol_spec = broker_config.get_symbol_specification(
                    symbol)
                self._symbol_specs[broker_key][symbol] = symbol_spec

    def render(self, renderer: ConsoleRenderer):
        """
        Render broker summary section.

        Args:
            renderer: Console renderer for formatting
        """
        if len(self._broker_scenario_map) <= 0:
            renderer.red("⚠️  No broker configuration available")
            return

        market_config = MarketConfigManager()
        i = 0
        for broker_key, broker_spec in self._broker_spec.items():
            market_type = market_config.get_market_type(broker_key.value)
            # Separator between broker blocks
            if i > 0:
                print(
                    "   ───────────────────────────────────────────────────────────────────────────")
                print("")

            # Render broker details
            broker_info = BrokerInfoRenderer.render_summary_table(
                broker_spec=broker_spec,
                scenarios=self._broker_scenario_list[broker_key],
                indent="   ",
                market_type=market_type,
            )
            print(broker_info)

            # If multiple symbols, show table
            print("")
            renderer.print_bold("   TRADED SYMBOLS")
            print("")
            symbol_spec_broker = self._symbol_specs[broker_key]
            symbols_table = BrokerInfoRenderer.render_symbols_table(
                symbol_specs=symbol_spec_broker,
                indent="   "
            )
            print(symbols_table)
            i += 1
