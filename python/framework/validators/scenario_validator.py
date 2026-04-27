"""
FiniexTestingIDE - Scenario Validator
Validates scenario configurations for consistency and compatibility

Key Validations:
- Scenario Names: Unique and non-empty
- Account Currency: Compatible with symbol (base or quote)
- Symbol Format: Authoritative from BrokerConfig; known-quote-suffix fallback

Usage:
    # Phase 0: Config validation
    ScenarioValidator.validate_scenario_names(scenarios, logger)
    ScenarioValidator.validate_account_currencies(scenarios, logger, broker_scenario_map)
"""

from typing import Dict, List
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.broker_config import BrokerType
from python.framework.types.scenario_types.scenario_set_types import BrokerScenarioInfo, SingleScenario
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

        Matches against known quote suffixes first; falls back to last 3 chars.
        Supports symbols with variable-length base currencies (e.g., DASHUSD).

        Examples:
            GBPUSD -> USD (you buy GBP with USD)
            EURUSD -> USD
            USDJPY -> JPY
            DASHUSD -> USD

        Args:
            symbol: Trading symbol (e.g., "GBPUSD", "DASHUSD")

        Returns:
            Quote currency (e.g., "USD")
        """
        known_quotes = ['USD', 'EUR', 'GBP', 'CAD', 'JPY', 'AUD']
        for quote in known_quotes:
            if symbol.upper().endswith(quote):
                return quote
        return symbol[-3:].upper()

    @staticmethod
    def detect_base_currency(symbol: str) -> str:
        """
        Detect base currency from trading symbol.

        Matches against known quote suffixes to derive base; falls back to all but last 3 chars.
        Supports symbols with variable-length base currencies (e.g., DASHUSD).

        Examples:
            GBPUSD -> GBP (you buy GBP with USD)
            EURUSD -> EUR
            USDJPY -> USD
            DASHUSD -> DASH

        Args:
            symbol: Trading symbol (e.g., "GBPUSD", "DASHUSD")

        Returns:
            Base currency (e.g., "GBP", "DASH")
        """
        known_quotes = ['USD', 'EUR', 'GBP', 'CAD', 'JPY', 'AUD']
        for quote in known_quotes:
            if symbol.upper().endswith(quote):
                return symbol[:-len(quote)].upper()
        return symbol[:-3].upper()

    @staticmethod
    def _get_symbol_currencies(
        symbol: str,
        scenario_broker_type: BrokerType,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
    ):
        """
        Return (base_currency, quote_currency) for a symbol.

        Uses authoritative BrokerConfig lookup when available; falls back to
        known-quote-suffix heuristic for unknown broker types.

        Args:
            symbol: Trading symbol (e.g. 'DASHUSD')
            scenario_broker_type: BrokerType assigned to the scenario
            broker_scenario_map: Map from BrokerType to BrokerScenarioInfo

        Returns:
            Tuple of (base_currency, quote_currency)
        """
        broker_info = broker_scenario_map.get(scenario_broker_type)
        if broker_info:
            try:
                spec = broker_info.broker_config.get_symbol_specification(symbol)
                return spec.base_currency, spec.quote_currency
            except ValueError:
                pass  # symbol not in broker — validate_scenario_symbols catches this
        return (
            ScenarioValidator.detect_base_currency(symbol),
            ScenarioValidator.detect_quote_currency(symbol),
        )

    @staticmethod
    def validate_scenario_symbols(
        scenarios: List[SingleScenario],
        logger: AbstractLogger,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
    ) -> None:
        """
        Validate each scenario's symbol is registered in its broker config.

        Side Effects:
        - Sets validation_result if invalid
        - Does NOT raise — marks scenarios as invalid instead

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for error messages
            broker_scenario_map: Map from BrokerType to BrokerScenarioInfo
        """
        for scenario in scenarios:
            broker_info = broker_scenario_map.get(scenario.broker_type)
            if not broker_info:
                continue
            try:
                broker_info.broker_config.get_symbol_specification(scenario.symbol)
            except ValueError:
                validation_result = ValidationResult(
                    is_valid=False,
                    scenario_name=scenario.name,
                    errors=[
                        f"Symbol '{scenario.symbol}' not found in broker configuration "
                        f"for '{scenario.data_broker_type}'. "
                        f"Check the 'symbols' section in the broker config."
                    ],
                    warnings=[]
                )
                scenario.validation_result.append(validation_result)
                logger.error(
                    f"❌ {scenario.name}: Symbol '{scenario.symbol}' not registered "
                    f"in broker '{scenario.data_broker_type}'"
                )

    @staticmethod
    def set_scenario_account_currency(
        logger: ScenarioLogger,
        scenarios: List[SingleScenario],
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
    ):
        """
        Derive account currency from balances dict for all scenarios.

        Reads 'balances' from trade_simulator_config and determines which
        currency key matches the symbol's base or quote currency.
        If balances is missing, skips — validate_account_currency() flags it later.

        Args:
            logger: Logger for debug messages
            scenarios: List of scenarios to process
            broker_scenario_map: Map from BrokerType to BrokerScenarioInfo for currency lookup
        """
        for scenario in scenarios:
            symbol = scenario.symbol
            balances = scenario.trade_simulator_config.get('balances', {})

            if not balances:
                # validation will flag this later
                continue

            # Explicit override or derive from balances keys + symbol
            explicit = scenario.trade_simulator_config.get('account_currency', '')
            if explicit:
                account_currency = explicit
            else:
                base, quote = ScenarioValidator._get_symbol_currencies(
                    symbol, scenario.broker_type, broker_scenario_map)
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
        logger: AbstractLogger,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
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
            broker_scenario_map: Map from BrokerType to BrokerScenarioInfo for currency lookup
        """
        balances = scenario.trade_simulator_config.get('balances', {})
        symbol = scenario.symbol

        # Reject missing balances
        if not balances:
            _, suggested = ScenarioValidator._get_symbol_currencies(
                symbol, scenario.broker_type, broker_scenario_map)
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

        base_currency, quote_currency = ScenarioValidator._get_symbol_currencies(
            symbol, scenario.broker_type, broker_scenario_map)

        balance_currencies = set(balances.keys())
        symbol_currencies = {base_currency, quote_currency}

        # Check at least one balance key matches base or quote
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
        logger: AbstractLogger,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
    ) -> None:
        """
        Validate account_currency for all scenarios.

        Calls validate_account_currency() for each scenario.
        Skips scenarios that are already invalid.

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for error messages
            broker_scenario_map: Map from BrokerType to BrokerScenarioInfo for currency lookup
        """
        for scenario in scenarios:
            ScenarioValidator.validate_account_currency(scenario, logger, broker_scenario_map)
