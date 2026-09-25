"""
FiniexTestingIDE - Application Configuration Types
Top-level Pydantic model for app_config.json.
"""
from typing import List

from python.framework.types.config_types.strict_config_model import StrictConfigModel

from python.framework.types.config_types.api_auth_config_types import ApiAuthConfig
from python.framework.types.config_types.autotrader_defaults_config_types import (
    AutotraderDefaultsConfig,
)
from python.framework.types.config_types.backtesting_config_types import BacktestingConfig
from python.framework.types.config_types.console_logging_config_types import ConsoleLoggingConfig
from python.framework.types.config_types.file_logging_config_types import FileLoggingConfig


class SharedPaths(StrictConfigModel):
    """Filesystem paths shared across both pipelines."""
    data_processed: str
    user_algo_dirs: List[str] = ['user_algos/']
    # Persistent run-results ledger (one parquet fragment per run; the
    # Parameter Optimization system reads it). A RECORD store — output of runs, so it
    # lives beside them under runs/ rather than in the data INPUT root (#486).
    run_ledger: str = 'runs/ledger'
    # Every configuration that can START a run, with an id, an index and a history (#538).
    # A RECORD store: it holds what a run was configured with, and it holds its OWN copy of
    # those bytes — a source may live in `user_algos/`, a separate repository this project
    # never writes into.
    run_configs: str = 'run_configs'
    # The patch of every dirty tree a run ran from, keyed by its SHA256 (#551). A RECORD
    # store: it is what lets a run from uncommitted code be restored to the code that ran.
    run_patches: str = 'run_patches'


class HistoryConfig(StrictConfigModel):
    """In-memory history retention limits (shared across both pipelines)."""
    bar_max_history: int = 1000
    order_history_max: int = 10000
    trade_history_max: int = 5000


class DevelopmentConfig(StrictConfigModel):
    """Development / debug flags."""
    dev_mode: bool = False


class AppConfig(StrictConfigModel):
    """
    Top-level model for app_config.json.

    Sections:
      - development, console_logging, file_logging: shared
      - paths, history: shared between both pipelines
      - api: HTTP API posture (whether a token is required)
      - autotrader: AutoTrader pipeline defaults
      - backtesting: Backtesting pipeline settings
    """
    version: str
    description: str = ''
    development: DevelopmentConfig = DevelopmentConfig()
    console_logging: ConsoleLoggingConfig
    file_logging: FileLoggingConfig
    paths: SharedPaths
    history: HistoryConfig = HistoryConfig()
    api: ApiAuthConfig = ApiAuthConfig()
    autotrader: AutotraderDefaultsConfig = AutotraderDefaultsConfig()
    backtesting: BacktestingConfig = BacktestingConfig()
