"""
Profile Loader
================
Loads GeneratorProfile artifacts from JSON files
and validates discovery fingerprints for freshness.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile

vLog = get_global_logger()


class ProfileLoader:
    """Loads and validates GeneratorProfile artifacts."""

    def __init__(self, logger: AbstractLogger = None):
        """
        Initialize profile loader.

        Args:
            logger: Logger instance (falls back to global logger)
        """
        self._logger = logger or vLog

    def load_profile(self, profile_path: str) -> GeneratorProfile:
        """
        Load a GeneratorProfile from JSON file.

        Args:
            profile_path: Path to profile JSON file

        Returns:
            GeneratorProfile instance
        """
        path = Path(profile_path)
        if not path.exists():
            raise FileNotFoundError(f"Profile not found: {path}")

        with open(path, 'r') as f:
            data = json.load(f)

        profile = GeneratorProfile.from_dict(data)

        self._logger.info(
            f"Loaded profile: {profile.profile_meta.symbol} "
            f"({profile.profile_meta.block_count} blocks, "
            f"{profile.profile_meta.generator_mode} mode)"
        )

        return profile

    def validate_fingerprints(
        self,
        profile: GeneratorProfile,
        cache_manager: Optional[DiscoveryCacheManager] = None
    ) -> List[str]:
        """
        Validate discovery fingerprints against current cache state.

        Args:
            profile: GeneratorProfile to validate
            cache_manager: Discovery cache manager (created if None)

        Returns:
            List of warning messages (empty if all fingerprints match)
        """
        if cache_manager is None:
            cache_manager = DiscoveryCacheManager(logger=self._logger)

        meta = profile.profile_meta
        current_fingerprints = cache_manager.get_fingerprints(
            meta.broker_type, meta.symbol
        )

        warnings = []

        for cache_name, stored_fp in meta.discovery_fingerprints.items():
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
