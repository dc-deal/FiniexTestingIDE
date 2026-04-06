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
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
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

        Account currency is explicitly configured per scenario.

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
        Derive account currency from balances dict for all scenarios.

        Reads 'balances' from trade_simulator_config and determines which
        currency key matches the symbol's base or quote currency.
        If balances is missing, skips — validate_account_currency() flags it later.
        """
        for scenario in scenarios:
            symbol = scenario.symbol
            balances = scenario.trade_simulator_config.get('balances', {})

            if not balances:
                # validation will flag this later
                continue

            # Derive account_currency from balances keys + symbol
            quote = ScenarioValidator.detect_quote_currency(symbol)
            base = ScenarioValidator.detect_base_currency(symbol)
            if quote in balances:
                account_currency = quote
            elif base in balances:
                account_currency = base
            elif len(balances) == 1:
                account_currency = list(balances.keys())[0]
            else:
                # ambiguous — validation will catch this
                continue

            logger.debug(
                f"💱 Account Currency: {account_currency} (derived from balances)"
            )

            scenario.account_currency = account_currency

    @staticmethod
    def validate_scenario_boundaries(
        scenarios: List[SingleScenario],
        logger: AbstractLogger
    ) -> None:
        """
        Validate each scenario has at least end_date or max_ticks.

        Without either boundary the tick loader has no stop condition.
        Both can be set (end_date limits data range, max_ticks limits processing).

        Side Effects:
        - Sets validation_result on invalid scenarios
        - Does NOT raise — marks scenarios as invalid instead

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for error messages
        """
        for scenario in scenarios:
            has_end_date = scenario.end_date is not None
            has_max_ticks = scenario.max_ticks is not None and scenario.max_ticks > 0

            if not has_end_date and not has_max_ticks:
                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[
                        f"Scenario '{scenario.name}' has neither end_date nor max_ticks. "
                        f"At least one is required to define the scenario boundary."
                    ],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                logger.error(
                    f"❌ {scenario.name}: No end_date and no max_ticks — "
                    f"at least one boundary is required"
                )

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
        Validate balances dict contains a currency matching the symbol.

        Rules:
        - 'balances' must be present and non-empty in trade_simulator_config
        - At least one key must match base or quote currency of the symbol

        Side Effects:
        - Sets validation_result if invalid
        - Does NOT raise - marks scenario as invalid instead

        Args:
            scenario: Scenario to validate
            logger: Logger for error messages
        """
        balances = scenario.trade_simulator_config.get('balances', {})
        symbol = scenario.symbol

        # Reject missing balances
        if not balances:
            try:
                suggested = ScenarioValidator.detect_quote_currency(symbol)
            except ValueError:
                suggested = 'USD'
            validation_result = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[
                    f"'balances' required in trade_simulator_config. "
                    f"Example: {{\"balances\": {{\"{suggested}\": 10000}}}}"
                ],
                warnings=[]
            )
            scenario.validation_result.append(validation_result)
            logger.error(
                f"❌ {scenario.name}: 'balances' missing in trade_simulator_config"
            )
            return

        # Validate symbol format and extract currencies
        try:
            base_currency = ScenarioValidator.detect_base_currency(symbol)
            quote_currency = ScenarioValidator.detect_quote_currency(symbol)
        except ValueError as e:
            validation_result = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[str(e)],
                warnings=[]
            )
            scenario.validation_result.append(validation_result)
            logger.error(f"❌ {scenario.name}: {str(e)}")
            return

        # Check at least one balance key matches base or quote
        balance_currencies = set(balances.keys())
        symbol_currencies = {base_currency, quote_currency}
        if not balance_currencies & symbol_currencies:
            validation_result = ValidationResult(
                is_valid=False,
                scenario_name=scenario.name,
                errors=[
                    f"No balance currency matches symbol {symbol}. "
                    f"Symbol uses {base_currency} (base) and {quote_currency} (quote). "
                    f"Balances contain: {list(balances.keys())}."
                ],
                warnings=[]
            )
            scenario.validation_result.append(validation_result)
            logger.error(
                f"❌ {scenario.name}: Balance currencies {list(balances.keys())} "
                f"don't match symbol {symbol} ({base_currency}/{quote_currency})"
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
