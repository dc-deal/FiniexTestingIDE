"""
Import Configuration Manager.

High-level API for import pipeline configuration.
Provides typed accessors for offset registry, paths, and processing settings.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from python.configuration.import_config_loader import ImportConfigFileLoader


class ImportConfigManager:
    """
    Manager for import pipeline configuration.

    Provides methods to:
    - Get default time offsets per broker_type
    - Get import pipeline paths (raw, output, finished)
    - Get test-specific paths for isolated testing
    - Get processing behavior settings
    """

    def __init__(self) -> None:
        """Initialize import config manager."""
        config, was_first_load = ImportConfigFileLoader.get_config()
        self._config = config
        if was_first_load:
            self._print_config_status()

    def _print_config_status(self) -> None:
        """Print import config status on first load."""
        move_files = self.get_move_processed_files()
        auto_bars = self.get_auto_render_bars()
        registry = self.get_offset_registry()
        offsets = ", ".join(
            f"{bt}: {e.get('default_offset_hours', 0):+d}h"
            for bt, e in registry.items()
        )
        print(
            f"📥 Import config loaded — "
            f"MOVE_FILES: {move_files}, AUTO_BARS: {auto_bars}, "
            f"OFFSETS: [{offsets}]"
        )

    # ============================================
    # Offset Registry
    # ============================================

    def get_offset_registry(self) -> Dict[str, Any]:
        """
        Get complete offset registry.

        Returns:
            Dict mapping broker_type to offset config entry
        """
        return self._config.get("offset_registry", {})

    def get_default_offset(self, broker_type: str) -> int:
        """
        Get default time offset for a broker_type.

        Args:
            broker_type: Normalized broker type identifier (e.g. "mt5", "kraken_spot")

        Returns:
            Default offset in hours (0 if broker_type not in registry)
        """
        registry = self.get_offset_registry()
        entry = registry.get(broker_type, {})
        return entry.get("default_offset_hours", 0)

    def get_offset_description(self, broker_type: str) -> Optional[str]:
        """
        Get human-readable offset description for a broker_type.

        Args:
            broker_type: Normalized broker type identifier

        Returns:
            Description string, or None if not configured
        """
        registry = self.get_offset_registry()
        entry = registry.get(broker_type)
        if entry is None:
            return None
        return entry.get("description")

    # ============================================
    # Paths — Production
    # ============================================

    def get_data_raw_path(self) -> str:
        """
        Get source directory for JSON tick exports.

        Returns:
            Path string for raw data directory
        """
        paths = self._config.get("paths", {})
        path = paths.get("data_raw")
        if not path:
            raise ValueError(
                "Missing required path 'data_raw' in import_config.json. "
                "Add to 'paths' section: \"data_raw\": \"data/raw\""
            )
        return path

    def get_import_output_path(self) -> str:
        """
        Get output directory for processed Parquet files.

        Returns:
            Path string for import output directory
        """
        paths = self._config.get("paths", {})
        path = paths.get("import_output")
        if not path:
            raise ValueError(
                "Missing required path 'import_output' in import_config.json. "
                "Add to 'paths' section: \"import_output\": \"data/processed\""
            )
        return path

    def get_data_finished_path(self) -> str:
        """
        Get directory for JSON files moved after successful import.

        Returns:
            Path string for finished data directory
        """
        paths = self._config.get("paths", {})
        path = paths.get("data_finished")
        if not path:
            raise ValueError(
                "Missing required path 'data_finished' in import_config.json. "
                "Add to 'paths' section: \"data_finished\": \"data/finished\""
            )
        return path

    # ============================================
    # Paths — Test Environment
    # ============================================

    def get_test_data_raw_path(self) -> str:
        """
        Get test source directory for isolated import testing.

        Returns:
            Path string for test raw data directory
        """
        test_paths = self._config.get("test_paths", {})
        path = test_paths.get("data_raw")
        if not path:
            raise ValueError(
                "Missing required test path 'data_raw' in import_config.json. "
                "Add to 'test_paths' section."
            )
        return path

    def get_test_import_output_path(self) -> str:
        """
        Get test output directory for isolated import testing.

        Returns:
            Path string for test import output directory
        """
        test_paths = self._config.get("test_paths", {})
        path = test_paths.get("import_output")
        if not path:
            raise ValueError(
                "Missing required test path 'import_output' in import_config.json. "
                "Add to 'test_paths' section."
            )
        return path

    def get_test_data_finished_path(self) -> str:
        """
        Get test finished directory for isolated import testing.

        Returns:
            Path string for test finished data directory
        """
        test_paths = self._config.get("test_paths", {})
        path = test_paths.get("data_finished")
        if not path:
            raise ValueError(
                "Missing required test path 'data_finished' in import_config.json. "
                "Add to 'test_paths' section."
            )
        return path

    # ============================================
    # Processing Settings
    # ============================================

    def get_processing_config(self) -> Dict[str, Any]:
        """
        Get processing configuration section.

        Returns:
            Processing config dict
        """
        return self._config.get("processing", {})

    def get_move_processed_files(self) -> bool:
        """
        Get move processed files setting.

        Returns:
            True if JSON files should be moved to finished/ after import
        """
        processing = self.get_processing_config()
        return processing.get("move_processed_files", True)

    def get_auto_render_bars(self) -> bool:
        """
        Get auto render bars setting.

        Returns:
            True if bars should be automatically rendered after tick import
        """
        processing = self.get_processing_config()
        return processing.get("auto_render_bars", True)
