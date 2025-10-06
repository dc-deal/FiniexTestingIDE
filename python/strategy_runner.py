"""
FiniexTestingIDE - Strategy Runner with VisualConsoleLogger
Compact, colorful logging output
"""

import os
import platform
import sys
import traceback

from python.framework.batch_orchestrator import BatchOrchestrator
from python.data_worker.data_loader.core import TickDataLoader
from python.framework.reporting.batch_summary import BatchSummary
from python.components.logger.scenario_performance_stats import PerformanceSummaryLog
from python.framework.trading_env.broker_config import BrokerConfig
from python.framework.trading_env.trade_simulator import TradeSimulator
from python.scenario.config_loader import ScenarioConfigLoader
from python.components.logger.bootstrap_logger import setup_logging
from python.configuration import AppConfigLoader

vLog = setup_logging(name="StrategyRunner")


def run_strategy_test() -> dict:
    """
    Main strategy testing function with visual output
    """

    vLog.info("🚀 Starting [BatchOrchestrator] strategy test", "StrategyRunner")

    try:
        # ============================================================
        # Load Application Configuration
        # ============================================================
        app_config_loader = AppConfigLoader()

        # Extract execution defaults
        default_parallel_scenarios = app_config_loader.get_default_parallel_scenarios()
        default_max_parallel_scenarios = app_config_loader.get_default_max_parallel_scenarios()
        default_parallel_workers = app_config_loader.get_default_parallel_workers()

        vLog.info(
            f"⚙️  App defaults: parallel_scenarios={default_parallel_scenarios}, "
            f"max_scenarios={default_max_parallel_scenarios}, "
            f"parallel_workers={default_parallel_workers}",
            "StrategyRunner"
        )

        # ============================================================
        # Config-Based Scenario loading
        # ============================================================

        vLog.info("📂 Loading scenario_set from config file...", "StrategyRunner")
        config_loader = ScenarioConfigLoader()
        scenario_set = config_loader.load_config("eurusd_3_windows.json")
        vLog.info(
            f"✅ Loaded {len(scenario_set)} scenario_set from config", "StrategyRunner")

        # ============================================
        # Create PerformanceSummaryLog
        # ============================================
        performance_log = PerformanceSummaryLog()
        vLog.info("✅ Created PerformanceSummaryLog")

        # ============================================
        # Initialize Components
        # ============================================
        # Data loader
        data_worker = TickDataLoader()

        # ============================================================
        # RUN BATCH TEST
        # ============================================================

        # Batch orchestrator (NEW: with TradeSimulator and PerformanceSummaryLog)
        orchestrator = BatchOrchestrator(
            scenario_set,
            data_worker,
            app_config_loader,
            performance_log
        )

        # Run test
        results = orchestrator.run()

        # ============================================
        # NEW (C#003): Direct Reporting via BatchSummary
        # ============================================
        try:
            summary = BatchSummary(
                performance_log=performance_log,
                app_config=app_config_loader
            )
            summary.render_all()

        except Exception as e:
            vLog.error(f"❌ Failed to render summary: {e}", exc_info=True)

        return results

    except FileNotFoundError as e:
        vLog.error(f"❌ Config file not found: {e}", "StrategyRunner")
        return {"success": False, "error": str(e), "results": []}
    except Exception as e:
        vLog.error(
            f"❌ Unexpected error:\n{traceback.format_exc()}", "StrategyRunner")
        return {"success": False, "error": str(e), "results": []}


if __name__ == "__main__":
    """Entry point"""

    # System info
    vLog.info(f"System: {platform.system()} {platform.release()}", "System")
    vLog.info(f"Python: {platform.python_version()}", "System")
    vLog.info(f"CPU Count: {os.cpu_count()}", "System")
    vLog.section_separator()

    # Run test
    results = run_strategy_test()

    # Exit with status
    if results.get('success', False):
        vLog.info("✅ All tests passed!", "StrategyRunner")
        exit(0)
    else:
        vLog.error("❌ Some tests failed!", "StrategyRunner")
        exit(1)
