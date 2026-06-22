"""
FiniexTestingIDE - Application Configuration Types
Top-level Pydantic model for app_config.json.
"""
from typing import List
from pydantic import BaseModel
from python.framework.types.config_types.console_logging_config_types import ConsoleLoggingConfig
from python.framework.types.config_types.file_logging_config_types import FileLoggingConfig
from python.framework.types.config_types.backtesting_config_types import BacktestingConfig
from python.framework.types.config_types.autotrader_defaults_config_types import AutotraderDefaultsConfig


class SharedPaths(BaseModel):
    """Filesystem paths shared across both pipelines."""
    data_processed: str
    user_algo_dirs: List[str] = ['user_algos/']
    # Persistent run-results ledger (one parquet fragment per run; the
    # Parameter Optimization system reads it). Data layer, survives log cleanup.
    run_results: str = 'data/run_results'


class HistoryConfig(BaseModel):
    """In-memory history retention limits (shared across both pipelines)."""
    bar_max_history: int = 1000
    order_history_max: int = 10000
    trade_history_max: int = 5000


class DevelopmentConfig(BaseModel):
    """Development / debug flags."""
    dev_mode: bool = False


class AppConfig(BaseModel):
    """
    Top-level model for app_config.json.

    Sections:
      - development, console_logging, file_logging: shared
      - paths, history: shared between both pipelines
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
    autotrader: AutotraderDefaultsConfig = AutotraderDefaultsConfig()
    backtesting: BacktestingConfig = BacktestingConfig()
