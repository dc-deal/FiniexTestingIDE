"""
FiniexTestingIDE - Scenario Validator
Validates scenario configurations for consistency and compatibility

Key Validations:
- Scenario Names: Unique and non-empty
- Account Currency: Compatible with symbol (base or quote)
- Symbol Format: Valid 6-character format
- Currency Consistency: For reporting purposes (legacy)

Usage:
    # Phase 0: Config validation
    ScenarioValidator.validate_scenario_names(scenarios, logger)
    ScenarioValidator.validate_account_currencies(scenarios, logger)
"""

from typing import Dict, List
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import ValidationResult
from python.framework.logging.abstract_logger import AbstractLogger


class ScenarioValidator:
    """
    Validates scenario set consistency and configuration.

    Responsibilities:
    - Validate scenario names (unique, non-empty)
    - Validate account_currency compatibility with symbols
    - Detect base and quote currencies from symbols
    - Legacy: Currency consistency checking
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
    def detect_base_currency(symbol: str) -> str:
        """
        Detect base currency from trading symbol.

        Base currency is always the first 3 characters of the symbol.

        Examples:
            GBPUSD -> GBP (you buy GBP with USD)
            EURUSD -> EUR
            USDJPY -> USD
            EURJPY -> EUR

        Args:
            symbol: Trading symbol (e.g., "GBPUSD")

        Returns:
            Base currency (e.g., "GBP")

        Raises:
            ValueError: If symbol format is invalid
        """
        if len(symbol) != 6:
            raise ValueError(
                f"Invalid symbol format: '{symbol}'. "
                f"Expected 6 characters (e.g., GBPUSD, EURUSD, USDJPY)"
            )

        return symbol[:3].upper()

    @staticmethod
    def get_currency_consistency(scenarios: List[SingleScenario]) -> str:
        """
        Get all scenarios use same quote currency.

        In auto mode, account currency = quote currency of symbol.

        Args:
            scenarios: List of scenarios to validate

        Returns:
            Quote currency if all scenarios use same one

        Raises:
            ValueError: If scenarios use different quote currencies
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

    @staticmethod
    def set_scenario_account_currency(logger: ScenarioLogger, scenarios: List[SingleScenario]):
        """
        Set Account Currency for all scenarios - will be sendt into process.
        """
        for scenario in scenarios:
            symbol = scenario.symbol
            # set in scenario_set or explicitly per scenario.
            account_currency = scenario.trade_simulator_config.get(
                'account_currency', 'auto')
            configured_account_currency = account_currency
            # === CURRENCY AUTO-DETECTION ===
            # If account_currency is "auto", extract from symbol (last 3 chars)
            if account_currency == "auto":
                detected_currency = ScenarioValidator.detect_quote_currency(
                    symbol)

                logger.debug(
                    f"💱 CURRENCY AUTO-DETECTION:\n"
                    f"   Symbol: {symbol} → Detected: {detected_currency}\n"
                    f"   Using: {detected_currency} (auto-detection overrides broker)\n"
                    f"   All P&L calculations will be in {detected_currency}."
                )

                account_currency = detected_currency
            else:
                # Explicit currency provided - just log it
                quote = ScenarioValidator.detect_quote_currency(symbol)
                base = ScenarioValidator.detect_base_currency(symbol)
                # try to match explicit currency
                detected_currency = quote
                if (base == account_currency):
                    detected_currency = base
                logger.info(
                    f"💱 Account Currency: {account_currency} (explicit configuration)"
                )

            scenario.account_currency = detected_currency
            scenario.configured_account_currency = configured_account_currency

    @staticmethod
    def validate_scenario_names(
        scenarios: List[SingleScenario],
        logger: AbstractLogger
    ) -> None:
        """
        Validate scenario names are present and unique.

        Rules:
        - Every scenario must have a non-empty name
        - All scenario names must be unique

        Side Effects:
        - Sets validation_result on invalid scenarios
        - Does NOT raise - marks scenarios as invalid instead

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for error messages
        """
        # Check for missing names
        for idx, scenario in enumerate(scenarios):
            if not scenario.name or scenario.name.strip() == "":
                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=f"<unnamed_{idx}>",
                    errors=[
                        "Scenario has no name. Every scenario must have a unique name."],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                logger.error(f"❌ Scenario at index {idx}: Missing name")

        # Check for duplicates
        name_counts: Dict[str, List[SingleScenario]] = {}
        for scenario in scenarios:
            if scenario.name and scenario.name.strip():  # Skip empty (already caught)
                if scenario.name not in name_counts:
                    name_counts[scenario.name] = []
                name_counts[scenario.name].append(scenario)

        # Mark all duplicates as invalid
        for name, single_scenario_list in name_counts.items():
            if len(single_scenario_list) > 1:
                for scenario in single_scenario_list:
                    validation_result = ValidationResult(
                        is_valid=False,
                        scenario_name=scenario.name,
                        errors=[
                            f"Duplicate scenario name '{name}'. All scenario names must be unique."],
                        warnings=[]
                    )
                    scenario.validation_result.append(validation_result)
                    logger.error(
                        f"❌ {scenario.name}: Duplicate name found ({len(single_scenario_list)} occurrences)")

    @staticmethod
    def validate_account_currency(
        scenario: SingleScenario,
        logger: AbstractLogger
    ) -> None:
        """
        Validate account_currency is either base or quote currency of symbol.

        Rules:
        - account_currency = "auto" → Always valid (uses quote currency in runtime)
        - account_currency = explicit → Must be base OR quote of symbol

        Auto-Detection Logic:
        - Auto mode ALWAYS uses quote currency (last 3 chars of symbol)
        - Example: EURGBP + auto → GBP (not EUR)

        Rationale:
        - Trade simulator can convert directly if account_currency is base or quote
        - External conversion rates needed otherwise (not supported)

        Side Effects:
        - Sets validation_result if invalid
        - Does NOT raise - marks scenario as invalid instead

        Args:
            scenario: Scenario to validate
            logger: Logger for error messages
        """
        account_currency = scenario.trade_simulator_config.get(
            "account_currency", "auto")

        # Auto mode always valid (uses quote currency in runtime)
        if account_currency.lower() == "auto":
            return

        # Validate symbol format and extract currencies
        symbol = scenario.symbol

        try:
            base_currency = ScenarioValidator.detect_base_currency(symbol)
            quote_currency = ScenarioValidator.detect_quote_currency(symbol)
        except ValueError as e:
            # Invalid symbol format
            validation_result = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[str(e)],
                warnings=[]
            )
            scenario.validation_result.append(validation_result)
            logger.error(f"❌ {scenario.name}: {str(e)}")
            return

        # Check if account_currency matches base OR quote
        account_currency_upper = account_currency.upper()

        if account_currency_upper not in [base_currency, quote_currency]:
            validation_result = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[
                    f"Account currency '{account_currency}' is neither base nor quote currency of symbol {symbol}. "
                    f"Symbol {symbol} uses {base_currency} (base) and {quote_currency} (quote). "
                    f"Account currency must be one of these, or use 'auto' (which selects {quote_currency})."
                ],
                warnings=[]
            )
            scenario.validation_result.append(validation_result)
            logger.error(
                f"❌ {scenario.name}: Account currency '{account_currency}' "
                f"not compatible with symbol {symbol} ({base_currency}/{quote_currency})"
            )

    @staticmethod
    def validate_account_currencies(
        scenarios: List[SingleScenario],
        logger: AbstractLogger
    ) -> None:
        """
        Validate account_currency for all scenarios.

        Calls validate_account_currency() for each scenario.
        Skips scenarios that are already invalid.

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for error messages
        """
        for scenario in scenarios:
            ScenarioValidator.validate_account_currency(scenario, logger)
