"""
FiniexTestingIDE - Strategy Runner mit VisualConsoleLogger
Kompakte, farbige Logging-Ausgabe
"""

import os
import platform
import logging

from python.framework.batch_orchestrator import BatchOrchestrator
from python.data_worker.data_loader.core import TickDataLoader
from python.scenario.config_loader import ScenarioConfigLoader
from python.components.logger.bootstrap_logger import setup_logging

vLog = setup_logging(name="StrategyRunner")

parallel_mode = False
max_workers = 4


def run_strategy_test() -> dict:
    """
    Main strategy testing function with visual output
    """

    vLog.info("🚀 Starting [BatchOrchestrator] strategy test", "StrategyRunner")

    try:
        # 1. Setup data loader
        loader = TickDataLoader()

        # ============================================================
        # Config-Based Scenario loading
        # ============================================================

        vLog.info("📂 Loading scenarios from config file...", "StrategyRunner")
        config_loader = ScenarioConfigLoader()
        scenarios = config_loader.load_config("eurusd_3_windows.json")
        vLog.info(
            f"✅ Loaded {len(scenarios)} scenarios from config", "StrategyRunner")

        # ============================================================
        # RUN BATCH TEST
        # ============================================================

        # Create BatchOrchestrator with loaded scenarios
        orchestrator = BatchOrchestrator(scenarios, loader)

        # Determine execution mode from first scenario's execution_config
        if scenarios and scenarios[0].execution_config:
            exec_config = scenarios[0].execution_config
            max_parallel = exec_config.get("max_parallel_scenarios", 4)
            max_workers = max_parallel

            vLog.info(
                f"⚙️  Execution Config: parallel={parallel_mode}, max_workers={max_workers}",
                "StrategyRunner"
            )

        # Run test
        results = orchestrator.run(parallel_mode, max_workers)

        # ============================================================
        # RESULTS OUTPUT (via VisualConsoleLogger)
        # ============================================================
        vLog.print_results_table(results)

        return results

    except FileNotFoundError as e:
        vLog.error(f"❌ Config file not found: {e}", "StrategyRunner")
        return {"success": False, "error": str(e), "results": []}
    except Exception as e:
        vLog.error(f"❌ Unexpected error: {e}", "StrategyRunner")
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
