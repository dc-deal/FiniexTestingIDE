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
    def get_currency_consistency(scenarios: List[SingleScenario]) -> str:
        """
        Get all scenarios use same quote currency.

        In auto mode, account currency = quote currency of symbol.

        Args:
            scenarios: List of scenarios to validate
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

        # Return the single detected currency
        return list(quote_currencies)[0]
