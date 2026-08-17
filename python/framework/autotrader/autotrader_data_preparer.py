"""
FiniexTestingIDE - AutoTrader Mock Session Data Preparer (#438)

Builds the single scenario an AutoTrader-mock profile describes (scenario_settings) and prepares
its data through the SHARED MountPreparer — the same index/validation stack the backtesting batch
uses. The result is one ProcessDataPackage (ticks + warmup bars + signal series) the mock session
replays through the live decision path. A config/data error excludes the single scenario → the
session ABORTS at startup (§35), mirroring the sim's per-scenario exclusion (§33).
"""

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.mount_preparer import MountPreparer
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_session_data_types import PreparedSessionData
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.utils.time_utils import parse_datetime


def build_scenario_from_config(config: AutoTraderConfig) -> SingleScenario:
    """
    Build the single SingleScenario an AutoTrader-mock profile describes (#438).

    The profile's scenario_settings maps 1:1 onto a simulation scenario's core fields (data window
    + balances + workers), so the mock replays scenario base data through the same preparation
    stack. The execution broker_type is the data-source default when scenario_settings omits
    data_broker_type (mock: data source == execution broker).

    Args:
        config: AutoTrader configuration (scenario_settings must be present — mock mode)

    Returns:
        The single SingleScenario (scenario_index 0)
    """
    settings = config.scenario_settings
    trade_simulator_config = {'balances': dict(settings.balances)}
    if settings.account_currency:
        trade_simulator_config['account_currency'] = settings.account_currency

    return SingleScenario(
        name=settings.name or config.name or config.symbol,
        scenario_index=0,
        symbol=config.symbol,
        data_broker_type=settings.data_broker_type or config.broker_type,
        start_date=parse_datetime(settings.start_date),
        end_date=parse_datetime(settings.end_date) if settings.end_date else None,
        max_ticks=settings.max_ticks,
        data_mode=settings.data_mode,
        data_sentiment_type=settings.data_sentiment_type,
        strategy_config=config.strategy_config,
        trade_simulator_config=trade_simulator_config,
        stress_test_config=settings.stress_test_config,
    )


def prepare_mock_session_data(
    config: AutoTraderConfig,
    logger: ScenarioLogger,
) -> PreparedSessionData:
    """
    Prepare the mock session's ticks + warmup bars + signal series via the shared MountPreparer.

    Builds the scenario the profile describes and runs it through the same Phase 0–5 stack the
    backtesting batch uses (broker/currency validation + index-resolved tick/bar/signal load). A
    config or data error excludes the single scenario → the session ABORTS at startup (§35), never
    at the first tick. The signal scenario map travels with the package so the end-of-session
    report renders the 📡 section from the same preparation the sim batch uses (#433).

    Args:
        config: AutoTrader configuration (mock mode; scenario_settings present)
        logger: Session logger

    Returns:
        The prepared data (ProcessDataPackage + signal scenario map)
    """
    scenario = build_scenario_from_config(config)
    preparer = MountPreparer(
        logger=logger,
        app_config=AppConfigManager(),
        requirements_collector=RequirementsCollector(logger=logger),
    )
    mount = preparer.prepare_mount([scenario], include_warmup_bars=False)
    package = mount.scenario_packages.get(scenario.scenario_index)
    if package is None or not scenario.is_valid():
        errors = '; '.join(
            e for v in scenario.validation_result if not v.is_valid for e in v.errors
        ) or 'no tick/signal data available for the configured window'
        raise ValueError(
            f"AutoTrader mock data preparation failed for '{scenario.name}': {errors}"
        )
    return PreparedSessionData(
        package=package, signal_scenario_map=mount.signal_scenario_map)
