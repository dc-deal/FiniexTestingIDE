"""
FiniexTestingIDE - Scenario Validator
Validates scenario configurations for consistency and compatibility

Key Validations:
- Currency Consistency: All scenarios must use same quote currency
- Auto-Detection: Extracts quote currency from symbol (last 3 chars)
- Feature Gate: Mixed currencies raise ValueError (MVP limitation)

Usage:
    validator = ScenarioValidator()
    detected_currency = validator.validate_currency_consistency(scenarios)
"""

from typing import List
from python.framework.types.scenario_set_types import SingleScenario


class ScenarioValidator:
    """
    Validates scenario set consistency.

    MVP: Enforces single quote currency across all scenarios.
    Post-MVP: Will support multi-currency with conversion rates.
    """

    @staticmethod
    def detect_quote_currency(symbol: str) -> str:
        """
        Detect quote currency from trading symbol.

        Quote currency is always the last 3 characters of the symbol.

        Examples:
            GBPUSD -> USD (you buy GBP with USD)
            EURUSD -> USD
            USDJPY -> JPY
            EURJPY -> JPY

        Args:
            symbol: Trading symbol (e.g., "GBPUSD")

        Returns:
            Quote currency (e.g., "USD")

        Raises:
            ValueError: If symbol format is invalid
        """
        if len(symbol) != 6:
            raise ValueError(
                f"Invalid symbol format: '{symbol}'. "
                f"Expected 6 characters (e.g., GBPUSD, EURUSD, USDJPY)"
            )

        return symbol[-3:].upper()

    @staticmethod
    def validate_currency_consistency(scenarios: List[SingleScenario]) -> str:
        """
        Validate all scenarios use same quote currency.

        In auto mode, account currency = quote currency of symbol.
        For MVP, all scenarios must have same quote currency to enable:
        - Consistent P&L calculations
        - Portfolio aggregation across scenarios
        - No currency conversion needed

        Args:
            scenarios: List of scenarios to validate

        Returns:
            Detected quote currency (e.g., "USD")

        Raises:
            ValueError: If scenarios have mixed quote currencies

        Example:
            scenarios = [
                SingleScenario(symbol="GBPUSD", ...),
                SingleScenario(symbol="EURUSD", ...)
            ]
            currency = validator.validate_currency_consistency(scenarios)
            # Returns: "USD" ✅

            scenarios = [
                SingleScenario(symbol="GBPUSD", ...),  # USD
                SingleScenario(symbol="USDJPY", ...)   # JPY
            ]
            # Raises: ValueError ❌
        """
        if not scenarios:
            raise ValueError("Cannot validate empty scenario list")

        # Extract quote currencies from all scenarios
        quote_currencies = set()
        symbol_currency_map = {}

        for scenario in scenarios:
            symbol = scenario.symbol
            quote = ScenarioValidator.detect_quote_currency(symbol)
            quote_currencies.add(quote)
            symbol_currency_map[symbol] = quote

        # Check consistency
        if len(quote_currencies) > 1:
            # Build detailed error message
            currency_groups = {}
            for symbol, currency in symbol_currency_map.items():
                if currency not in currency_groups:
                    currency_groups[currency] = []
                currency_groups[currency].append(symbol)

            error_details = []
            for currency, symbols in currency_groups.items():
                error_details.append(f"  {currency}: {', '.join(symbols)}")

            raise ValueError(
                "❌ Mixed quote currencies detected in scenario set!\n"
                f"\n"
                f"Found {len(quote_currencies)} different currencies:\n"
                f"{chr(10).join(error_details)}\n"
                f"\n"
                f"MVP Limitation: All scenarios must use same quote currency.\n"
                f"→ Split scenarios into separate sets by currency:\n"
                f"   - eurusd_gbpusd_scenarios.json (USD)\n"
                f"   - usdjpy_eurjpy_scenarios.json (JPY)\n"
                f"\n"
                f"Post-MVP: Multi-currency support with conversion rates."
                f"Why?: Current Main issue is aggregation split on Currency in Portfolio Summary / Coversion mechanic / no Multi portfolio currency support"
            )

        # Return the single detected currency
        return list(quote_currencies)[0]
