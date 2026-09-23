"""
The scenario set as a RUNTIME object — it opens a run, it does not describe one.

`ScenarioSet` lived in `framework/types/scenario_types/` and is not a type: it takes an
`AppConfigManager`, mints a run id, creates the run directory, writes the run header, registers
the run in the index and in the run-config store, and copies the config snapshot. CLAUDE.md §6
says where such a thing belongs — a bundle of runtime COLLABORATORS lives beside the code that
BUILDS it, never in `framework/types/` — and here it sits beside its loader.

**What the move bought, measured 2026-09-22 (#395).** Because it lived among the dataclasses, it
dragged the run index, the broker config, a scenario logger, the run-config store, the signal
coverage report and git into every module that only wanted `ScenarioSetMetadata` — the pure
dataclass the `list` command renders. That import cost 904 modules and 1.97 s against 232 and
0.49 s for the data half alone, and on this tree the difference is nearly all OURS: 375 of our
own files cost 13.48 ms each across the bridged mount, against 0.67 ms for a third-party module
on the container's own disk (§42).
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.logging.system_info_writer import write_system_version_parameters
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.report_types import ParentKind, RunHeader, RunReporting
from python.framework.types.config_types.robustness_config_types import RobustnessConfig
from python.framework.types.log_layout_types import MOUNT_BUILD_LOG, RUN_TYPE_SIMULATION
from python.framework.types.run_config_types import RunConfigKind
from python.framework.types.scenario_types.scenario_set_types import (
    LoadedScenarioConfig,
    SingleScenario,
)
from python.framework.types.scenario_types.window_set_types import WindowSet
from python.framework.utils.git_info_utils import get_git_commit
from python.framework.utils.run_id_utils import mint_run_id
from python.framework.utils.scenario_set_utils import (
    SIM_CONFIG_SNAPSHOT,
    ScenarioSetUtils,
)


def _register_run_config(source: Path) -> str:
    """
    Record which configuration this run is starting from, and return its identity.

    Never fatal. A config the store cannot register is a config the run can still execute — the
    per-run snapshot beside the header is the evidence either way, and refusing to start a
    backtest because a parquet index could not be written would be the wrong trade entirely.

    Args:
        source: The scenario set file this run was commissioned with

    Returns:
        The registered content id, or an empty string when it could not be registered
    """
    try:
        store = RunConfigStore(Path(AppConfigManager().get_run_configs_path()))
        entry = store.register(Path(source), RunConfigKind.SCENARIO_SET)
        # Registration says the content exists; this says a RUN used it. Two steps on
        # purpose — a config listed by the finder is registered without being run.
        store.note_run(entry.config_id)
        return entry.config_id
    except (OSError, ValueError, KeyError):
        return ''


class ScenarioSet:
    """Self-contained scenario set with its own logging infrastructure"""

    def __init__(self, scenario_config: LoadedScenarioConfig, app_config: AppConfigManager,
                 sweep_id: Optional[str] = None, mount_only: bool = False,
                 reporting: RunReporting = RunReporting.EXPECTED):
        """
        Args:
            scenario_config: The loaded scenario set
            app_config: Application configuration
            sweep_id: When given, this set's runs nest under that sweep's directory
            mount_only: This set exists solely to BUILD A DATA MOUNT (#419), not to run. It
                writes its one log flat into the sweep directory as mount_build.log and opens
                no run directory — a shared data load is not a run, and a directory shaped like
                one is indistinguishable from a real run in the API's index. No summary logger
                either: there is no batch report to summarise
            reporting: Whether this run will be given a report coordinator. Declared here
                because only the CALLER knows — a test that stops after `orchestrator.run()`
                passes NONE, so its empty artifact list reads as intended rather than as a
                run that died before reporting (#475)
        """

        self.scenario_set_name = scenario_config.scenario_set_name
        self._scenarios = scenario_config.scenarios
        self.config_path = scenario_config.config_path
        self.app_config = app_config
        self._generator_profiles = scenario_config.generator_profiles
        self._generator_profile_paths = scenario_config.generator_profile_paths
        self._robustness = scenario_config.robustness or RobustnessConfig()
        # Where this run's logs land, from config (file_logging.run_logs) — the same paths the
        # API reads. A sweep's combinations nest under their sweep id, a standalone run does
        # not: a directory level, while the run TYPE stays `simulation` for both.
        run_logs = app_config.get_file_logging_config_object().run_logs
        self._log_root = (run_logs.sweeps / sweep_id if sweep_id
                          else Path(run_logs.simulation))

        # ScenarioSet creates its own loggers
        self._run_timestamp = datetime.now(
            timezone.utc)
        # Minted ONCE here and handed to every logger of this run — they must share a directory.
        # The owner dir is passed so a taken id is re-minted rather than silently joined.
        self._run_id = mint_run_id(self._run_timestamp, self._log_root / self.scenario_set_name)

        self.logger = ScenarioLogger(
            scenario_set_name=self.scenario_set_name,
            scenario_name='global_log',
            run_timestamp=self._run_timestamp,
            run_id=self._run_id,
            log_root_override=self._log_root,
            use_global_log_level_for_console=True,
            flat_log_filename=MOUNT_BUILD_LOG if mount_only else None
        )
        # The run header goes down FIRST, before anything else can fail. A mount build gets
        # none: it has no run directory, because it is not a run.
        # Only the COMMIT is needed here — `get_git_commit()` costs 68 ms where the full
        # read costs ~2.0 s, and the header has no use for branch / dirty (§42).
        if not mount_only and self.logger.get_log_dir() is not None:
            # Registered HERE, at the start, for the same reason the header is written here: the
            # configuration a run used is the one it STARTED with, and a file edited while the
            # run is in flight must not change what the run says it ran (#538). Registration is
            # idempotent — unchanged content writes no second copy.
            config_id = _register_run_config(self.config_path)
            header = RunHeader(
                run_id=self._run_id,
                start_time=self._run_timestamp,
                run_type=RUN_TYPE_SIMULATION,
                run_name=self.scenario_set_name,
                parent_id=sweep_id,
                # Written WITH the id, never after it: a parent id whose kind is unknown is a
                # row nothing can group correctly (#386).
                parent_kind=ParentKind.SWEEP if sweep_id else None,
                config_snapshot=SIM_CONFIG_SNAPSHOT,
                config_id=config_id,
                app_version=app_config.get_version(),
                git_commit=get_git_commit(),
                reporting=reporting,
            )
            RunIndex(app_config.get_file_logging_config_object().run_index).register_run(
                header, self.logger.get_log_dir())

        # A mount build produces no batch report, so a summary logger would only ever write its
        # own header — which is exactly what it used to do.
        self.printed_summary_logger = None if mount_only else ScenarioLogger(
            scenario_set_name=self.scenario_set_name,
            scenario_name='summary',
            run_timestamp=self._run_timestamp,
            run_id=self._run_id,
            log_root_override=self._log_root
        )

    @property
    def run_id(self) -> str:
        """The identity this run is known by — directory name, artifact key, ledger column."""
        return self._run_id

    @property
    def run_timestamp(self) -> datetime:
        """Expose run_timestamp for easy access"""
        return self._run_timestamp

    @property
    def log_root(self) -> Path:
        """The category root this run's logs land under (file_logging.run_logs)."""
        return self._log_root

    def copy_config_snapshot(self) -> None:
        """
        Copy config snapshot to log directory.
        Call explicitly before execution starts.
        """
        # copy file snapshot to log folder
        scenario_set_utils = ScenarioSetUtils(
            config_snapshot_path=self.config_path,
            scenario_log_path=self.logger.get_log_dir(),
            file_name=SIM_CONFIG_SNAPSHOT,
        )
        scenario_set_utils.copy_config_snapshot()

        # Copy generator profile files for Profile Runs
        if self._generator_profile_paths:
            self._copy_generator_profiles()

    def _copy_generator_profiles(self) -> None:
        """Copy generator profile JSON files to scenario_run_configs/ in log directory."""
        log_dir = self.logger.get_log_dir()
        run_configs_dir = log_dir / 'scenario_run_configs'
        run_configs_dir.mkdir(exist_ok=True)

        for profile_path in self._generator_profile_paths:
            try:
                shutil.copy2(profile_path, run_configs_dir / profile_path.name)
            except Exception as e:
                vLog = get_global_logger()
                vLog.warning(
                    f'⚠️ Failed to copy profile {profile_path.name}: {e}')

    def write_scenario_system_info_log(self):
        """
        Write System Information for Performance Tracking
        """
        if self.app_config.get_logging_write_system_info():
            system_info_logger = ScenarioLogger(
                scenario_set_name=self.scenario_set_name,
                scenario_name='system_info',
                run_timestamp=self.logger.get_run_timestamp(),
                run_id=self._run_id,
                log_root_override=self._log_root
            )

            write_system_version_parameters(system_info_logger)
            system_info_logger.close(flush_buffer=True)

    def get_valid_scenarios(self) -> List[SingleScenario]:
        """Get all scenarios that passed validation."""
        return [scenario for scenario in self._scenarios if scenario.is_valid()]

    def get_failed_scenarios(self) -> List[SingleScenario]:
        """Get all scenarios that passed validation."""
        return [scenario for scenario in self._scenarios if not scenario.is_valid()]

    def get_all_scenarios(self) -> List[SingleScenario]:
        """Get all scenarios that passed validation."""
        return self._scenarios

    def get_generator_profiles(self) -> Optional[List[WindowSet]]:
        """
        Get generator window sets for Profile Runs.

        Returns:
            List of WindowSet objects, or None for normal runs
        """
        return self._generator_profiles

    def get_robustness_config(self) -> RobustnessConfig:
        """
        Get the set-wide robustness config (#367).

        Returns:
            RobustnessConfig (a disabled default when the set has no robustness block)
        """
        return self._robustness
