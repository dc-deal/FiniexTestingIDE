"""
Profile Loader
================
Loads profile artifacts from JSON files into WindowSets
and validates discovery fingerprints for freshness.
"""

import json
from pathlib import Path
from typing import List, Optional

from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_types.window_set_types import WindowSet
from python.scenario.generator.window_set_serializer import WindowSetSerializer

vLog = get_global_logger()


class ProfileLoader:
    """Loads and validates profile artifacts into WindowSets."""

    def __init__(self, logger: AbstractLogger = None):
        """
        Initialize profile loader.

        Args:
            logger: Logger instance (falls back to global logger)
        """
        self._logger = logger or vLog

    def load_profile(self, profile_path: str) -> WindowSet:
        """
        Load a profile artifact from JSON file into a WindowSet.

        Args:
            profile_path: Path to profile JSON file

        Returns:
            WindowSet instance
        """
        path = Path(profile_path)
        if not path.exists():
            raise FileNotFoundError(f"Profile not found: {path}")

        with open(path, 'r') as f:
            data = json.load(f)

        window_set = WindowSetSerializer.from_profile_dict(data)

        self._logger.info(
            f"Loaded profile: {window_set.symbol} "
            f"({window_set.block_count} blocks, "
            f"{window_set.mode} mode)"
        )

        return window_set

    def validate_fingerprints(
        self,
        window_set: WindowSet,
        cache_manager: Optional[DiscoveryCacheManager] = None
    ) -> List[str]:
        """
        Validate discovery fingerprints against current cache state.

        Args:
            window_set: WindowSet to validate
            cache_manager: Discovery cache manager (created if None)

        Returns:
            List of warning messages (empty if all fingerprints match)
        """
        if cache_manager is None:
            cache_manager = DiscoveryCacheManager(logger=self._logger)

        current_fingerprints = cache_manager.get_fingerprints(
            window_set.broker_type, window_set.symbol
        )

        warnings = []

        for cache_name, stored_fp in window_set.discovery_fingerprints.items():
            current_fp = current_fingerprints.get(cache_name)

            if current_fp is None:
                warnings.append(
                    f"Discovery cache '{cache_name}' not found — "
                    f"profile may be stale"
                )
            elif current_fp != stored_fp:
                warnings.append(
                    f"Discovery cache '{cache_name}' fingerprint mismatch — "
                    f"config has changed since profile generation"
                )

        if warnings:
            for w in warnings:
                self._logger.warning(f"⚠️ {w}")
        else:
            self._logger.info('✅ All discovery fingerprints match')

        return warnings
