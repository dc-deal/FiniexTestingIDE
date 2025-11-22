

from logging import config
from pathlib import Path
import shutil

from python.components.logger.bootstrap_logger import get_logger
from python.configuration.app_config_manager import AppConfigManager

vLog = get_logger()


class ScenarioSetUtils:

    def __init__(self, config_snapshot_path: Path, scenario_log_path: Path):
        config = AppConfigManager()
        file_logger_config = config.get_file_logging_config_object()
        self.file_logging_enabled = file_logger_config.is_file_logging_enabled()

        self.scenario_log_path = scenario_log_path
        self.config_snapshot_path = config_snapshot_path
        self.config_copied = False

    def copy_config_snapshot(self):
        """Copy scenario config file as snapshot (global only)."""
        if not self.file_logging_enabled:
            return
        try:
            if self.config_snapshot_path.exists():
                scource_path = self.config_snapshot_path
                target_path = self.scenario_log_path / "config.json"
                shutil.copy2(scource_path,
                             target_path)
                self.config_copied = True
                vLog.debug(
                    f"✅ Copied scenarios config from {self.config_snapshot_path}")
            else:
                # Log warning in file
                vLog.warning(
                    f"⚠️  WARNING: Config file not found for snapshot: {self.source_config_path}\n\n"
                )
        except Exception as e:
            vLog.error(
                f"❌ ERROR: Failed to copy config snapshot: {e}\n\n"
            )
