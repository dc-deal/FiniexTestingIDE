"""
FiniexTestingIDE - Composite Definition Manager

Reads which configuration values define a composite (#615) — the platform's defaults in
`configs/composite_definition.json`, under the operator's override in the workspace — and hands
each pipeline its section with the section's fingerprint, which every composite key carries.
"""

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional

from pydantic import ValidationError

from python.configuration.data_home import workspace_path
from python.framework.types.composite_types import CompositeDocument
from python.framework.types.config_types.composite_definition_config_types import (
    CompositeDefinitionConfig,
    CompositeTier,
)
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint
from python.framework.utils.config_merge_utils import deep_merge, is_config_isolation_active

_CONFIG_PATH = 'configs/composite_definition.json'
_USER_CONFIG_NAME = 'composite_definition.json'


class CompositeDefinitionManager:
    """
    The composite definition, read once through the usual cascade.

    The workspace override is laid over the shipped file rule by rule, so it changes a tier or
    adds an exception without copying the rest. The floor cannot be reached from either file.
    """

    _config: Optional[CompositeDefinitionConfig] = None
    _lock = Lock()

    def __init__(self, config_path: Optional[str] = None, user_config_path: Optional[str] = None):
        """
        Args:
            config_path: The shipped definition; the module's default when None
            user_config_path: The workspace override; the data home's copy when None
        """
        self._config_path = config_path or _CONFIG_PATH
        self._user_config_path = user_config_path or str(workspace_path(_USER_CONFIG_NAME))

    def get_rules(self, document: CompositeDocument) -> Dict[str, CompositeTier]:
        """
        One pipeline's rules.

        Args:
            document: Which rendered document the rules apply to

        Returns:
            The rules, path → tier
        """
        config = self._load()
        return dict(config.scenario if document is CompositeDocument.SCENARIO else config.session)

    def get_fingerprint(self, document: CompositeDocument) -> str:
        """
        The fingerprint of one pipeline's section — what a key made under it carries.

        Covering the section rather than the file keeps a backtest's keys unchanged when only the
        session half is edited, and the other way round.

        Args:
            document: Which rendered document the rules apply to

        Returns:
            SHA256 hex digest of the section's rules
        """
        return generate_config_fingerprint(
            {path: tier.value for path, tier in self.get_rules(document).items()})

    def get_definition(self) -> CompositeDefinitionConfig:
        """
        The whole definition, as each run freezes it.

        Returns:
            The validated definition
        """
        return self._load()

    @classmethod
    def reload(cls) -> None:
        """
        Forget the cached definition, so the next read takes the files as they are now.
        """
        with cls._lock:
            cls._config = None

    def _load(self) -> CompositeDefinitionConfig:
        """
        Read the definition once.

        Returns:
            The merged definition
        """
        config = CompositeDefinitionManager._config
        if config is not None:
            return config
        with CompositeDefinitionManager._lock:
            if CompositeDefinitionManager._config is None:
                CompositeDefinitionManager._config = self._read()
            return CompositeDefinitionManager._config

    def _read(self) -> CompositeDefinitionConfig:
        """
        Load the shipped definition and lay the workspace override over it.

        Returns:
            The validated definition
        """
        raw = self._read_json(Path(self._config_path))
        user_path = Path(self._user_config_path)
        if user_path.exists() and not is_config_isolation_active():
            raw = deep_merge(raw, self._read_json(user_path))
        try:
            return CompositeDefinitionConfig(**raw)
        except ValidationError as error:
            raise ValueError(f'Invalid composite definition ({self._config_path}): {error}') from error

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        """
        Read one definition file.

        Args:
            path: The file

        Returns:
            Its content
        """
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
