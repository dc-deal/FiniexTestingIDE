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

from typing import Any, Dict, List, Set, Tuple

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.data_preparation.broker_scenario_info import BrokerScenarioInfo
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.broker_config import BrokerType
from python.framework.types.trading_env_types.order_types import OrderFeature
from python.framework.validators.component_metadata_advisory import check_market_fit
from python.framework.validators.decision_logic_hook_validator import check_order_feature_declaration
from python.framework.validators.order_feature_validator import (
    features_the_venue_lacks,
    order_feature_refusal,
)
from python.framework.types.scenario_types.scenario_set_types import (
    SingleScenario,
)
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.utils.account_currency_resolver import resolve_account_currency


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
                validation_result = ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.ERROR, check='symbol_unknown',
                        domain=ValidationDomain.BROKER,
                        message=f"Symbol '{scenario.symbol}' not found in broker configuration "
                            f"for '{scenario.data_broker_type}'. "
                            f"Check the 'symbols' section in the broker config.", scope=scenario.name)])
                scenario.validation_result.append(validation_result)
                logger.error(
                    f"❌ {scenario.name}: Symbol '{scenario.symbol}' not registered "
                    f"in broker '{scenario.data_broker_type}'"
                )

    @staticmethod
    def validate_swap_modes(
        scenarios: List[SingleScenario],
        logger: AbstractLogger,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
    ) -> None:
        """
        Validate each scenario's symbol uses a swap_mode the engine models.

        The swap engine computes only POINTS (NONE = no swap); any other mode — or an
        unparseable config string mapped to UNKNOWN — would silently accrue wrong or
        zero overnight financing. An unmodeled mode marks the scenario invalid (it is
        then excluded); it does NOT abort the batch (§33).

        Side Effects:
        - Sets validation_result(is_valid=False) on scenarios with an unmodeled swap_mode
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
                spec = broker_info.broker_config.get_symbol_specification(scenario.symbol)
            except ValueError:
                continue  # missing symbol — validate_scenario_symbols handles it
            if spec.swap_mode.is_implemented:
                continue
            validation_result = ValidationResult(scenario.name, [
                ValidationFinding(
                    severity=Severity.ERROR, check='swap_mode_unsupported',
                    domain=ValidationDomain.BROKER,
                    message=f"Symbol '{scenario.symbol}' uses swap_mode "
                        f"'{spec.swap_mode.value}' which the swap engine does not model. "
                        f"Supported modes: 'points', 'none'.", scope=scenario.name)])
            scenario.validation_result.append(validation_result)
            logger.error(
                f"❌ {scenario.name}: swap_mode '{spec.swap_mode.value}' not modeled "
                f"(supported: points, none)"
            )

    @staticmethod
    def validate_order_features(
        scenarios: List[SingleScenario],
        logger: AbstractLogger,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo],
        exception_possible: bool = True,
    ) -> None:
        """
        Refuse a scenario whose logic declares an order feature its venue's API does not hold (#625).

        A declared exception (`features_beyond_venue`) lets it through. The exception is held
        narrow: one that no scenario of the set needs, or that its venue holds anyway, is refused,
        because a broad one would record a deviation that never happened.

        Side Effects:
        - Appends an ERROR finding to scenario.validation_result, which excludes that scenario

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for the factory and the errors
            broker_scenario_map: Map from BrokerType to BrokerScenarioInfo
            exception_possible: Whether the refusal may point at the exception: a mock session
                comes through here too, and it cannot declare one
        """
        decision_factory = DecisionLogicFactory(logger)
        needed: Set[OrderFeature] = set()
        for scenario in scenarios:
            broker_info = broker_scenario_map.get(scenario.broker_type)
            logic_type = (scenario.strategy_config or {}).get('decision_logic_type', '')
            if not broker_info or not logic_type:
                continue
            try:
                logic_class, _ = decision_factory.resolve_logic_class(logic_type)
            except Exception:
                continue  # resolution failures reported by requirements/factory

            declaration_error = check_order_feature_declaration(logic_class)
            if declaration_error:
                ScenarioValidator._refuse_scenario(
                    scenario, logger, declaration_error, check='order_feature_undeclared')
                continue
            venue_lacks = features_the_venue_lacks(
                logic_class.get_required_order_features(
                    (scenario.strategy_config or {}).get('decision_logic_config', {})),
                broker_info.broker_config.get_order_capabilities())
            needed.update(venue_lacks)
            refusal = order_feature_refusal(
                logic_type, broker_info.broker_config.adapter.get_capability_label(),
                [feature for feature in venue_lacks
                 if feature not in scenario.features_beyond_venue],
                exception_possible=exception_possible)
            if refusal:
                ScenarioValidator._refuse_scenario(
                    scenario, logger, refusal, check='order_feature_refused')

        for scenario in scenarios:
            unneeded = [feature.value for feature in scenario.features_beyond_venue
                        if feature not in needed]
            if unneeded:
                ScenarioValidator._refuse_scenario(
                    scenario, logger,
                    f'features_beyond_venue names {unneeded}, which no scenario of this set '
                    f'needs: no logic declares it, or its venue holds it anyway. The exception '
                    f'is recorded with the run, so it says only what really happened.',
                    check='feature_beyond_venue_unneeded')

    @staticmethod
    def _refuse_scenario(
        scenario: SingleScenario,
        logger: AbstractLogger,
        message: str,
        check: str,
    ) -> None:
        """
        Exclude one scenario with an ERROR finding on its configuration.

        Args:
            scenario: The scenario
            logger: Where the error is logged
            message: The sentence it is refused with
            check: The finding's check id
        """
        scenario.validation_result.append(ValidationResult(scenario.name, [
            ValidationFinding(severity=Severity.ERROR, check=check,
                              domain=ValidationDomain.BROKER, message=message,
                              scope=scenario.name)]))
        logger.error(f'{scenario.name}: {message}')

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
        market_config = MarketConfigManager()
        for scenario in scenarios:
            symbol = scenario.symbol
            balances = scenario.trade_simulator_config.get('balances', {})

            if not balances:
                # validation will flag this later
                continue

            base, quote = ScenarioValidator._get_symbol_currencies(
                symbol, scenario.broker_type, broker_scenario_map)

            # The one rule both pipelines share: explicit override, else derived from the
            # balances and the symbol; spot settles in the quote currency.
            trading_model = market_config.get_trading_model(scenario.broker_type.value)
            resolution = resolve_account_currency(
                scenario.trade_simulator_config.get('account_currency', ''),
                balances, base, quote, trading_model)
            if resolution.account_currency is None:
                # ambiguous — validation will catch this
                continue

            # A base-denominated spot account would mix units: gross P&L in base, fees in
            # quote. The rule normalized it to quote; warn — the operator sees it took effect.
            if resolution.normalized_from is not None:
                logger.warning(
                    f"💱 {scenario.name}: account_currency '{resolution.normalized_from}' "
                    f"normalized to quote '{quote}' — spot settles in the quote currency"
                )
                scenario.validation_result.append(ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.WARNING, check='account_currency_normalized',
                        domain=ValidationDomain.CONFIG,
                        message=f"account_currency '{resolution.normalized_from}' normalized to "
                            f"quote '{quote}' — spot settles in the quote currency, not the base "
                            f"asset (base-denominated P&L would mix units).", scope=scenario.name)]))
            account_currency = resolution.account_currency

            logger.debug(
                f'💱 Account Currency: {account_currency} (derived from balances)'
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
                validation_result = ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.ERROR, check='scenario_boundary',
                        domain=ValidationDomain.CONFIG,
                        message=f"Scenario '{scenario.name}' has neither end_date nor max_ticks. "
                            f"At least one is required to define the scenario boundary.", scope=scenario.name)])
                scenario.validation_result.append(validation_result)
                logger.error(
                    f'❌ {scenario.name}: No end_date and no max_ticks — '
                    f'at least one boundary is required'
                )

    @staticmethod
    def check_parameter_constancy(
        scenarios: List[SingleScenario],
    ) -> Tuple[bool, List[str]]:
        """
        Check the cascade-resolved strategy_config is constant across windows (#367).

        A robustness comparison (IS/OOS, multi-window distribution) is only fair if the
        parameters are held CONSTANT across the windows — otherwise a performance difference
        may stem from a silent parameter drift, not the market window. This compares the full
        resolved strategy_config (the existing `is_mixed_*` flags only compare the logic-type
        string / worker count, not the parameters). Pure — no side effects; the advisory is
        emitted by the PostRunValidator, the fact is consumed by the robustness report.

        Args:
            scenarios: The robustness run's scenarios (compared against the first)

        Returns:
            Tuple of (is_constant, drifting_window_names) — names whose params differ from the first
        """
        if len(scenarios) < 2:
            return True, []
        reference = scenarios[0].strategy_config
        drifting = [s.name for s in scenarios[1:] if s.strategy_config != reference]
        return (len(drifting) == 0, drifting)

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
            if not scenario.name or scenario.name.strip() == '':
                validation_result = ValidationResult(f'<unnamed_{idx}>', [
                    ValidationFinding(
                        severity=Severity.ERROR, check='scenario_name_missing',
                        domain=ValidationDomain.CONFIG,
                        message='Scenario has no scenario_name. Every scenario must have a unique one.', scope=f'<unnamed_{idx}>')])
                scenario.validation_result.append(validation_result)
                logger.error(f'❌ Scenario at index {idx}: Missing name')

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
                    validation_result = ValidationResult(scenario.name, [
                        ValidationFinding(
                            severity=Severity.ERROR, check='scenario_name_duplicate',
                            domain=ValidationDomain.CONFIG,
                            message=f"Duplicate scenario name '{name}'. All scenario names must be unique.", scope=scenario.name)])
                    scenario.validation_result.append(validation_result)
                    logger.error(
                        f'❌ {scenario.name}: Duplicate name found ({len(single_scenario_list)} occurrences)')

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
            validation_result = ValidationResult(scenario.name, [
                ValidationFinding(
                    severity=Severity.ERROR, check='balances_missing',
                    domain=ValidationDomain.CONFIG,
                    message=f"'balances' required in trade_simulator_config. "
                        f"Example: {{\"balances\": {{\"{suggested}\": 10000}}}}", scope=scenario.name)])
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
            validation_result = ValidationResult(scenario.name, [
                ValidationFinding(
                    severity=Severity.ERROR, check='balance_currency_mismatch',
                    domain=ValidationDomain.CONFIG,
                    message=f'No balance currency matches symbol {symbol}. '
                        f'Symbol uses {base_currency} (base) and {quote_currency} (quote). '
                        f'Balances contain: {list(balances.keys())}.', scope=scenario.name)])
            scenario.validation_result.append(validation_result)
            logger.error(
                f"❌ {scenario.name}: Balance currencies {list(balances.keys())} "
                f"don't match symbol {symbol} ({base_currency}/{quote_currency})"
            )
            return

        # An explicit account_currency override must be the symbol's base or quote.
        # Cross-currency settlement (account ≠ base ≠ quote) needs an FX rate source
        # and is not supported in V1. Reject here — otherwise tick_value raises
        # mid-run, or the balance is silently zeroed (balance = balances.get(
        # account_currency, 0.0)) and the scenario produces a no-op run.
        explicit = scenario.trade_simulator_config.get('account_currency', '')
        if explicit and explicit not in symbol_currencies:
            validation_result = ValidationResult(scenario.name, [
                ValidationFinding(
                    severity=Severity.ERROR, check='account_currency_invalid',
                    domain=ValidationDomain.CONFIG,
                    message=f"account_currency '{explicit}' is neither base ({base_currency}) "
                        f"nor quote ({quote_currency}) of symbol {symbol}. "
                        f"Cross-currency settlement is not supported (V1).", scope=scenario.name)])
            scenario.validation_result.append(validation_result)
            logger.error(
                f"❌ {scenario.name}: account_currency '{explicit}' incompatible with "
                f"symbol {symbol} ({base_currency}/{quote_currency})"
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

    @staticmethod
    def validate_scenario_parameters(
        scenarios: List[SingleScenario],
        logger: AbstractLogger,
    ) -> None:
        """
        Validate each scenario's strategy parameters against the component schemas.

        Resolves the decision-logic class and every configured worker class (class
        resolution only — no instantiation) and validates their config against
        get_parameter_schema(): type, range, required, AND unknown keys. A typo'd
        parameter (e.g. 'min_confidenceX') is otherwise silently ignored at runtime,
        so this gate is the only place it surfaces.

        A class-resolution failure (unknown type, missing file) is left to the
        requirements collector / factory, which report it with full context — so this
        gate only flags genuine parameter errors, never produces a false exclusion.

        Side Effects:
        - Sets validation_result(is_valid=False) on scenarios with a bad parameter
        - Does NOT raise — marks scenarios as invalid instead

        Args:
            scenarios: List of scenarios to validate
            logger: Logger for the factories and error messages
        """
        decision_factory = DecisionLogicFactory(logger)
        worker_factory = WorkerFactory(logger)

        for scenario in scenarios:
            strategy = scenario.strategy_config or {}
            if not strategy.get('decision_logic_type', ''):
                continue

            errors = ScenarioValidator._collect_parameter_errors(
                strategy, decision_factory, worker_factory)
            if errors:
                scenario.validation_result.append(ValidationResult(scenario.name, [
                    ValidationFinding(
                        severity=Severity.ERROR, check='parameter_validation',
                        domain=ValidationDomain.CONFIG,
                        message=_m, scope=scenario.name)
                    for _m in errors]))
                for error in errors:
                    logger.error(f'❌ {scenario.name}: {error}')

    @staticmethod
    def validate_market_fit(
        scenarios: List[SingleScenario],
        logger: AbstractLogger,
    ) -> None:
        """
        Advise when a scenario's decision logic runs outside its recommended market/instrument.

        Resolves the decision-logic class (class resolution only — `get_metadata()` is a
        classmethod, so nothing is instantiated) and records the mismatch as an ADVISORY
        finding. This used to be a `logger.warning` inside the subprocess, which made the run
        report classify a validator's verdict as an unadjudicated log line — and it never
        needed the run at all, since every input is static config.

        Side Effects:
        - Appends advisory (WARNING) findings to scenario.validation_result — never an error,
          so no scenario is ever excluded by this check

        Args:
            scenarios: List of scenarios to advise on
            logger: Logger for the factory
        """
        decision_factory = DecisionLogicFactory(logger)

        for scenario in scenarios:
            strategy = scenario.strategy_config or {}
            logic_type = strategy.get('decision_logic_type', '')
            if not logic_type:
                continue
            try:
                logic_class, _ = decision_factory.resolve_logic_class(logic_type)
            except Exception:
                continue  # resolution failures reported by requirements/factory

            findings = check_market_fit(
                logic_class.get_metadata(), logic_type,
                scenario.broker_type, scenario.symbol, scenario.name)
            if findings:
                scenario.validation_result.append(
                    ValidationResult(scenario.name, findings))

    @staticmethod
    def _collect_parameter_errors(
        strategy: Dict[str, Any],
        decision_factory: DecisionLogicFactory,
        worker_factory: WorkerFactory,
    ) -> List[str]:
        """
        Validate decision + worker params against their schemas; collect error messages.

        Args:
            strategy: The scenario's strategy_config (decision + worker types + configs)
            decision_factory: Factory for resolving the decision-logic class
            worker_factory: Factory for resolving worker classes

        Returns:
            List of parameter-error messages (empty if all params valid)
        """
        errors: List[str] = []
        logic_type = strategy.get('decision_logic_type', '')
        logic_config = strategy.get('decision_logic_config', {})
        worker_instances = strategy.get('worker_instances', {})
        workers_config = strategy.get('workers', {})

        # --- Decision logic params ---
        try:
            logic_class, logic_source = decision_factory.resolve_logic_class(logic_type)
        except Exception:
            return errors  # resolution failures reported by requirements/factory

        try:
            logic_class.validate_parameter_schema(logic_config, strict=True)
        except ValueError as e:
            errors.append(str(e))

        # --- Worker params (USER refs resolve against the logic's source dir) ---
        worker_base_path = logic_source.parent if logic_source else None
        for instance_name, worker_type in worker_instances.items():
            try:
                worker_class, _ = worker_factory.resolve_worker_class(
                    worker_type, base_path=worker_base_path)
            except Exception:
                continue  # resolution failures reported by requirements/factory
            try:
                worker_class.validate_parameter_schema(
                    workers_config.get(instance_name, {}), strict=True)
            except ValueError as e:
                errors.append(str(e))

        return errors
