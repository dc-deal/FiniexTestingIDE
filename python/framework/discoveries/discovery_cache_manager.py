# ============================================
# python/framework/discoveries/discovery_cache_manager.py
# ============================================
"""
Unified Discovery Cache Manager
================================
Central coordinator for all discovery cache systems.

Provides a single entry point for rebuilding, clearing, and
inspecting all discovery caches (Extreme Moves, Coverage/Gap,
and MarketAnalyzer cache).

Used by:
- bar_importer.py (rebuild after bar import)
- discoveries_cli.py (CLI cache commands)
"""

from typing import Dict

from python.framework.discoveries.data_coverage.data_coverage_report_cache import DataCoverageReportCache
from python.framework.discoveries.discovery_cache import DiscoveryCache
from python.framework.discoveries.market_analyzer_cache import MarketAnalyzerCache
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger


class DiscoveryCacheManager:
    """
    Central coordinator for all discovery cache systems.

    Delegates to individual cache implementations and aggregates
    results for unified status reporting and rebuild triggers.
    """

    def __init__(self, logger: AbstractLogger = None):
        """
        Initialize with optional logger.

        Args:
            logger: Logger instance (falls back to global logger)
        """
        self._logger = logger or get_global_logger()
        self._discovery_cache = DiscoveryCache(logger=self._logger)
        self._data_coverage_cache = DataCoverageReportCache()
        self._market_analyzer_cache = MarketAnalyzerCache(logger=self._logger)

    def rebuild_all(self, force: bool = False) -> Dict[str, Dict[str, int]]:
        """
        Rebuild all discovery caches.

        Args:
            force: Force rebuild even if cache is valid

        Returns:
            Dict mapping cache name to {generated, skipped, failed} stats
        """
        results: Dict[str, Dict[str, int]] = {}

        self._logger.info("Rebuilding all discovery caches...")

        results["coverage"] = self._data_coverage_cache.build_all(
            force_rebuild=force
        )
        results["extreme_moves"] = self._discovery_cache.build_all(
            force_rebuild=force
        )
        results["market_analyzer"] = self._market_analyzer_cache.build_all(
            force_rebuild=force
        )

        total_generated = sum(r["generated"] for r in results.values())
        total_skipped = sum(r["skipped"] for r in results.values())
        total_failed = sum(r["failed"] for r in results.values())

        self._logger.info(
            f"All caches rebuilt: {total_generated} generated, "
            f"{total_skipped} skipped, {total_failed} failed"
        )

        return results

    def status(self) -> Dict[str, Dict]:
        """
        Get status overview of all discovery caches.

        Returns:
            Dict mapping cache name to status dict
        """
        return {
            "coverage": self._data_coverage_cache.get_cache_status(),
            "extreme_moves": self._discovery_cache.get_cache_status(),
            "market_analyzer": self._market_analyzer_cache.get_cache_status(),
        }

    def clear_all(self) -> Dict[str, int]:
        """
        Clear all discovery caches.

        Returns:
            Dict mapping cache name to number of files deleted
        """
        results: Dict[str, int] = {}

        results["coverage"] = self._data_coverage_cache.clear_cache()
        results["extreme_moves"] = self._discovery_cache.clear_cache()
        results["market_analyzer"] = self._market_analyzer_cache.clear_cache()

        total = sum(results.values())
        self._logger.info(
            f"All caches cleared: {total} files deleted "
            f"(coverage: {results['coverage']}, "
            f"extreme_moves: {results['extreme_moves']}, "
            f"market_analyzer: {results['market_analyzer']})"
        )

        return results
