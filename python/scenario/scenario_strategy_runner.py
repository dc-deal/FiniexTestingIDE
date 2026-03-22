"""
FiniexTestingIDE - Strategy Runner with VisualConsoleLogger
Compact, colorful logging output

ENTRY POINT: Initializes logger with auto-init via bootstrap_logger
"""

from typing import List

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.scenario_types.scenario_set_types import LoadedScenarioConfig, ScenarioSet
from python.scenario.generator.profile_loader import ProfileLoader
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.batch.batch_report_coordinator import BatchReportCoordinator

from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


def run_strategy_test(scenario_set_json: str):
    """
    Main strategy testing function with visual output

    Args:
        scenario_set_json: Config filename (e.g., "eurusd_3_windows.json")
    """

    try:
        vLog.info("🚀 Starting [BatchOrchestrator] strategy test")

        # ============================================================
        # Load Application Configuration
        # ============================================================
        app_config_loader = AppConfigManager()

        # Extract execution defaults
        default_parallel_scenarios = app_config_loader.get_default_parallel_scenarios()
        default_max_parallel_scenarios = app_config_loader.get_default_max_parallel_scenarios()
        default_parallel_workers = app_config_loader.get_default_parallel_workers()

        vLog.info(
            f"📋 Execution config: "
            f"Parallel Scenarios={default_parallel_scenarios}, "
            f"Max Workers={default_max_parallel_scenarios}, "
            f"Worker Parallelism={default_parallel_workers}"
        )

        # ============================================================
        # Load Scenario Configuration
        # ============================================================
        scenario_config_loader = ScenarioConfigLoader()

        # scenario_set_json is now passed as parameter
        scenario_config_data = scenario_config_loader.load_config(
            scenario_set_json)

        vLog.info(
            f"📂 Loaded scenario set: {scenario_set_json} ({len(scenario_config_data.scenarios)} scenarios)"
        )

        initialize_batch_and_run(scenario_config_data, app_config_loader)

    except Exception as e:
        vLog.hard_error(
            f"Unexpected error during startup",
            exception=e
        )


def run_profile_test(scenario_set_json: str, profile_paths: List[str]):
    """
    Run a Profile Run — loads profile blocks as scenarios.

    Accepts one or more profile paths. Multiple profiles are merged
    into a single batch with globally unique scenario indices.

    Args:
        scenario_set_json: Scenario set config (for global strategy/execution config)
        profile_paths: List of paths to GeneratorProfile JSON files
    """
    try:
        vLog.info("🚀 Starting [BatchOrchestrator] Profile Run")

        app_config_loader = AppConfigManager()

        # Load all profiles
        loader = ProfileLoader()
        profiles = []
        for path in profile_paths:
            profile = loader.load_profile(path)

            # Validate fingerprints per profile
            fingerprint_warnings = loader.validate_fingerprints(profile)
            if fingerprint_warnings:
                vLog.warning(
                    f"⚠️ Profile {profile.profile_meta.symbol} has "
                    f"{len(fingerprint_warnings)} fingerprint warning(s) — "
                    f"discovery configs may have changed since generation"
                )

            profiles.append(profile)

        # Create merged scenarios from all profiles + global config
        scenario_config_loader = ScenarioConfigLoader()
        scenario_config_data = scenario_config_loader.load_from_profiles(
            profiles, scenario_set_json
        )

        total_blocks = sum(p.profile_meta.block_count for p in profiles)
        symbols = sorted(set(p.profile_meta.symbol for p in profiles))
        vLog.info(
            f"📂 Profile Run: {len(profiles)} profile(s), "
            f"{total_blocks} blocks, symbols: {', '.join(symbols)}"
        )

        initialize_batch_and_run(scenario_config_data, app_config_loader)

    except Exception as e:
        vLog.hard_error(
            f"Unexpected error during Profile Run startup",
            exception=e
        )


def initialize_batch_and_run(scenario_config_data: LoadedScenarioConfig, app_config_loader: AppConfigManager):
    try:
        # ScenarioSet erstellt sich selbst mit eigenen Loggern
        scenario_set = ScenarioSet(scenario_config_data, app_config_loader)

        vLog.info("📊 Writing system & version information...")
        scenario_set.write_scenario_system_info_log()
        scenario_set.copy_config_snapshot()

        # ============================================================
        # Execute Batch via Orchestrator
        # ============================================================
        orchestrator = BatchOrchestrator(
            scenario_set,
            app_config_loader
        )

        # Run test
        batch_execution_summary = orchestrator.run()

        # ============================================
        # Generate and log batch report
        # ============================================
        report_coordinator = BatchReportCoordinator(
            batch_execution_summary=batch_execution_summary,
            scenario_set=scenario_set,
            app_config=app_config_loader
        )
        report_coordinator.generate_and_log()

    except FileNotFoundError as e:
        vLog.config_error(
            f"Config file not found: {e}",
            file_path=str(e)
        )

    except Exception as e:
        vLog.hard_error(
            f"Unexpected error during strategy test",
            exception=e
        )
