"""
FiniexTestingIDE - Data Collector Configuration Manager
Loads data_collector_config.json with user-override support (#120): where the import fetches the
collector's tick packages from.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from python.configuration.data_home import workspace_path
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_types.data_collector_config_types import (
    ActivePackageSource,
    DataCollectorConfig,
)
from python.framework.utils.config_merge_utils import deep_merge, is_config_isolation_active

vLog = get_global_logger()


class DataCollectorConfigManager:
    """
    Loads and provides typed access to the data collector's package service settings.

    Globally available: instantiate where needed, no injection.
    """

    def __init__(
        self,
        config_path: str = 'configs/data_collector_config.json',
        user_config_path: Optional[str] = None,
    ):
        """
        Args:
            config_path: Path to the base configuration
            user_config_path: Path to the user override; the workspace copy under the data home
                when omitted
        """
        self._config_path = Path(config_path)
        self._user_config_path = (Path(user_config_path) if user_config_path is not None
                                  else workspace_path('data_collector_config.json'))
        self._config: DataCollectorConfig = DataCollectorConfig.model_validate(self._load())

    def get_config(self) -> DataCollectorConfig:
        """
        The merged, typed configuration.

        Returns:
            The configuration
        """
        return self._config

    def resolve_package_source(self) -> Optional[ActivePackageSource]:
        """
        The package service the import fetches from, address and credential file together.

        Returns:
            The active source, None when its address is empty — nothing is fetched then
        """
        packages = self._config.packages
        endpoint = packages.get_active_endpoint()
        if not endpoint.base_url:
            return None
        return ActivePackageSource(
            name=packages.active,
            base_url=endpoint.base_url.rstrip('/'),
            credentials_file=endpoint.credentials_file,
            connection=packages.connection)

    def _load(self) -> Dict[str, Any]:
        """
        Load the base configuration and merge the user override over it. With config isolation
        active (the test session) the workspace copy is never read, so no test reaches the
        collector.

        Returns:
            The merged raw configuration
        """
        if not self._config_path.exists():
            vLog.warning(f'No {self._config_path} — no package source, nothing is fetched')
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
                f'Invalid JSON in user data collector config: {self._user_config_path}\n'
                f'Error: {error}\n'
                f'Fix the syntax or remove the file.'
            )

        vLog.debug(f'Merged user data collector config from {self._user_config_path}')
        return deep_merge(base, override)
