"""
FiniexTestingIDE - Application Configuration Types
Top-level Pydantic model for app_config.json.
"""
from pathlib import Path
from typing import List

from pydantic import field_validator

from python.framework.types.config_types.strict_config_model import StrictConfigModel

from python.framework.types.config_types.api_auth_config_types import ApiConfig
from python.framework.types.config_types.autotrader_defaults_config_types import (
    AutotraderDefaultsConfig,
)
from python.framework.types.config_types.backtesting_config_types import BacktestingConfig
from python.framework.types.config_types.console_logging_config_types import ConsoleLoggingConfig
from python.framework.types.config_types.docs_search_config_types import DocsSearchConfig
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
    # those bytes — a source may live in `user_algos/`, a separate repository.
    run_configs: str = 'run_configs'
    # The patch of every dirty tree of THIS repository a run ran from, keyed by its SHA256
    # (#551) — any other repository keeps its own inside itself. A RECORD store: it is what lets a
    # run from uncommitted code be restored to the code that ran.
    run_patches: str = 'run_patches'
    # The directory of every configuration that can start a run (#554). A DERIVED store: one
    # cached reading per configuration file, deletable at any time.
    config_directory: str = 'data/runtime/config_directory'


class ActivitiesConfig(StrictConfigModel):
    """What runs on this machine (#603)."""
    # ONE directory per machine, outside every code tree: several checkouts or worktrees on one
    # machine, and on a server one code tree per release tag, all list their work in it. An
    # absolute path stands for itself; `~` is the home of the user the process runs as.
    directory: str = '~/.finiex/activities'
    # The environment variable whose value names the console session an activity was started
    # from, so two people or two parallel workers on one machine are told apart. Empty: none.
    session_env_var: str = ''

    @field_validator('directory')
    @classmethod
    def _directory_stands_for_itself(cls, value: str) -> str:
        """
        Refuse a directory that would split the list by working directory.

        Args:
            value: The configured directory

        Returns:
            The directory, when it is absolute or starts at the home directory
        """
        if not value.strip():
            raise ValueError('activities.directory is empty — name one directory per machine')
        if not (value.startswith('~') or Path(value).is_absolute()):
            raise ValueError(
                f'activities.directory {value!r} is relative — each process would resolve it '
                'against its own working directory, and the one list would split in several')
        return value


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
      - docs_search: what the terminal search covers beyond the documentation
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
    activities: ActivitiesConfig = ActivitiesConfig()
    api: ApiConfig = ApiConfig()
    docs_search: DocsSearchConfig = DocsSearchConfig()
    autotrader: AutotraderDefaultsConfig = AutotraderDefaultsConfig()
    backtesting: BacktestingConfig = BacktestingConfig()
