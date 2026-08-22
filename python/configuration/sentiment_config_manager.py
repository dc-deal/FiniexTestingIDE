"""
FiniexTestingIDE - Sentiment Configuration Manager
Loads sentiment_config.json with user-override support (#141 Part 2a).

The signal side's counterpart to MarketConfigManager: market_config.json holds market and
broker facts that a scenario points at with broker_type; this holds producer and pipeline
facts that a scenario points at with data_sentiment_type. Both pipelines read it — a
source's cadence and staleness describe the SOURCE, not the run, so a simulation needs
them as much as a live session does.
"""

import json
from pathlib import Path
from typing import Any, Dict

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_types.sentiment_config_types import SentimentConfig
from python.framework.utils.config_merge_utils import deep_merge, is_config_isolation_active

vLog = get_global_logger()


class SentimentConfigManager:
    """
    Loads and provides typed access to the sentiment source registry and transport settings.

    Globally available (§28): instantiate where needed, no injection.
    """

    def __init__(
        self,
        config_path: str = 'configs/sentiment_config.json',
        user_config_path: str = 'user_configs/sentiment_config.json',
    ):
        """
        Initialize the sentiment config manager.

        Args:
            config_path: Path to the base configuration
            user_config_path: Path to the user override
        """
        self._config_path = Path(config_path)
        self._user_config_path = Path(user_config_path)
        self._config: SentimentConfig = SentimentConfig.model_validate(self._load())

    def get_config(self) -> SentimentConfig:
        """The merged, typed configuration."""
        return self._config

    def get_source_config(self, pipeline_id: str):
        """
        Registered facts for one signal source.

        Args:
            pipeline_id: The source's id (= a scenario's data_sentiment_type)

        Returns:
            Its SentimentSourceConfig, or None when the source is not registered
        """
        return self._config.get_source(pipeline_id)

    def resolve_api_token(self, credentials_filename: str) -> str:
        """
        Read the producer's API token via the credential cascade.

        Cascade: user_configs/credentials/ → configs/credentials/, matching every other
        credential in the project. An EMPTY token is a valid answer and means "send no
        Authorization header" — which is correct while the producer has no authentication.
        A missing file is not an error for the same reason: no auth, nothing to read.

        Args:
            credentials_filename: File name inside the credentials directory

        Returns:
            The token, or '' when none is configured
        """
        for directory in ('user_configs/credentials', 'configs/credentials'):
            path = Path(directory) / credentials_filename
            if not path.exists():
                continue
            try:
                with open(path, 'r') as handle:
                    return json.load(handle).get('api_token', '')
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    f"Invalid JSON in credentials file: {path}\n"
                    f"Error: {error}\n"
                    f"Fix the syntax or remove the file."
                )
        return ''

    def _load(self) -> Dict[str, Any]:
        """
        Load the base configuration and merge the user override over it.

        Tests run with config isolation active (tests/conftest.py) so the user workspace
        never bleeds into the suite — a personal endpoint must not decide a test outcome.

        Returns:
            The merged raw configuration
        """
        if not self._config_path.exists():
            vLog.warning(
                f"No {self._config_path} — sentiment sources unregistered, transports off")
            return {}

        with open(self._config_path, 'r') as handle:
            base = json.load(handle)

        if not self._user_config_path.exists() or is_config_isolation_active():
            return base

        try:
            with open(self._user_config_path, 'r') as handle:
                override = json.load(handle)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Invalid JSON in user sentiment config: {self._user_config_path}\n"
                f"Error: {error}\n"
                f"Fix the syntax or remove the file."
            )

        vLog.debug(f"Merged user sentiment config from {self._user_config_path}")
        return deep_merge(base, override)
