

import shutil
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_types.file_logging_config_types import FileLoggingConfig

vLog = get_global_logger()

# The name a run's configuration snapshot is filed under, per pipeline. ONE source, because
# the run header DECLARES this name before the file is copied and a reader joins the two —
# they agreed only by coincidence while the header wrote a literal and the copier derived the
# name from the LOGGING configuration, so changing `file_logging.scenario.file_name_prefix`
# would have made every new header name a file that is not there.
SIM_CONFIG_SNAPSHOT = 'scenario_config.json'
LIVE_CONFIG_SNAPSHOT = 'autotrader_config.json'


class ScenarioSetUtils:

    def __init__(
        self,
        config_snapshot_path: Path,
        scenario_log_path: Path,
        file_name: str,
    ):
        app_config = AppConfigManager()
        file_logger_config: FileLoggingConfig = app_config.get_file_logging_config_object()
        self.file_logging_enabled = file_logger_config.is_file_logging_enabled()

        self.scenario_log_path = scenario_log_path
        self.config_snapshot_path = config_snapshot_path
        self.config_copied = False
        self._file_name = file_name

    def copy_config_snapshot(self):
        """Copy scenario config file as snapshot (global only)."""
        if not self.file_logging_enabled:
            return
        try:
            if self.config_snapshot_path.exists():
                source_path = self.config_snapshot_path
                target_path = self.scenario_log_path / self._file_name
                shutil.copy2(source_path,
                             target_path)
                self.config_copied = True
                vLog.debug(
                    f'✅ Copied scenarios config from {self.config_snapshot_path}')
            else:
                # Log warning in file
                vLog.warning(
                    f'⚠️  WARNING: Config file not found for snapshot: {self.config_snapshot_path}\n\n'
                )
        except Exception as e:
            vLog.error(
                f'❌ ERROR: Failed to copy config snapshot: {e}\n\n'
            )
