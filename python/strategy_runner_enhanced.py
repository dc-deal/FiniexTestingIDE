"""
FiniexTestingIDE - Strategy Runner mit Performance-Statistiken
Zeigt detaillierte Sequential vs Parallel Metriken
"""

import logging
import os
import platform

from python.framework.batch_orchestrator import BatchOrchestrator
from python.framework.types import TestScenario
from python.data_worker.data_loader.core import TickDataLoader
from python.scenario.config_loader import ScenarioConfigLoader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

parallel_mode = False
max_workers = 4


def run_strategy_test() -> dict:
    """
    Main strategy testing function with detailed statistics output
    """

    logger.info(f"🚀 Starting [BatchOrchestrator] strategy test")

    try:
        # 1. Setup data loader
        loader = TickDataLoader()

       # ============================================================
        # Config-Based Scenario loading
        # ============================================================

        logger.info("📂 Loading scenarios from config file...")
        config_loader = ScenarioConfigLoader()
        scenarios = config_loader.load_config("eurusd_3_windows.json")
        logger.info(f"✅ Loaded {len(scenarios)} scenarios from config")

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

            logger.info(
                f"⚙️  Execution Config: parallel={parallel_mode}, max_workers={max_workers}")

        # Run test
        results = orchestrator.run(parallel_mode, max_workers)

        # ============================================================
        # RESULTS SUMMARY & STATISTICS
        # ============================================================

        # Extract worker statistics if available
        worker_stats = extract_worker_statistics(orchestrator)

        # Print detailed results with worker stats
        print_detailed_results(results, worker_stats)

        return results

    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        raise


def extract_worker_statistics(orchestrator) -> dict:
    """
    Extract worker statistics from orchestrator

    Returns:
        Dictionary with worker stats or empty dict if not available
    """
    if not hasattr(orchestrator, '_last_orchestrator'):
        return {}

    worker_coordinator = orchestrator._last_orchestrator
    if not hasattr(worker_coordinator, 'get_statistics'):
        return {}

    return worker_coordinator.get_statistics()


def print_worker_statistics(stats: dict):
    """
    Print worker coordinator statistics

    Args:
        stats: Statistics dictionary from WorkerCoordinator
    """
    if not stats:
        return

    print("\n" + "-" * 60)
    print("📊 WORKER COORDINATOR STATISTICS")
    print("-" * 60)
    print(f"  Ticks processed:       {stats.get('ticks_processed', 0):,}")
    print(f"  Worker calls:          {stats.get('worker_calls', 0):,}")
    print(f"  Decisions made:        {stats.get('decisions_made', 0):,}")

    # Parallel-specific stats
    if 'parallel_execution_time_saved_ms' in stats:
        time_saved = stats['parallel_execution_time_saved_ms']
        avg_saved = stats.get('avg_time_saved_per_tick_ms', 0)

        print("\n" + "  " + "⚡ PARALLELIZATION METRICS")
        print(f"  Total time saved:      {time_saved:.2f}ms")
        print(f"  Avg saved per tick:    {avg_saved:.3f}ms")

        if time_saved > 0:
            print(f"  Status:                ✅ Parallel was FASTER")
        elif time_saved < 0:
            print(f"  Status:                ⚠️  Sequential was FASTER (overhead)")
        else:
            print(f"  Status:                ≈ No difference")


def print_detailed_results(results: dict, worker_stats: dict = None):
    """
    Print detailed results with statistics
    Compatible with BatchOrchestrator output format
    """
    print("\n" + "=" * 60)
    print("🎉 EXECUTION RESULTS")
    print("=" * 60)

    # Basic results
    print(f"✅ Success:            {results.get('success', True)}")
    print(f"📊 Scenarios:          {results.get('scenarios_count', 0)}")
    print(f"⏱️  Execution time:     {results.get('execution_time', 0):.2f}s")
    print(f"⚙️  Parallel Mode:     {parallel_mode}")
    print(f"⚙️  Max. Workers:      {max_workers}")

    if "error" in results:
        print(f"❌ Error:              {results['error']}")
        return

    # Scenario details
    if "results" in results and len(results["results"]) > 0:
        print("\n" + "-" * 60)
        print("SCENARIO DETAILS")
        print("-" * 60)

        for i, scenario_result in enumerate(results["results"], 1):
            scenario_name = scenario_result.get('scenario_name', 'Unknown')
            print(f"\n📋 Scenario {i}: {scenario_name}")
            print(
                f"  Symbol:             {scenario_result.get('symbol', 'N/A')}")
            print(
                f"  Ticks processed:    {scenario_result.get('ticks_processed', 0):,}")
            print(
                f"  Signals generated:  {scenario_result.get('signals_generated', 0)}")
            print(
                f"  Signal rate:        {scenario_result.get('signal_rate', 0):.1%}")

            # Worker statistics per scenario (if available)
            if 'worker_statistics' in scenario_result:
                stats = scenario_result['worker_statistics']
                print(
                    f"  Worker calls:       {stats.get('worker_calls', 0):,}")
                print(
                    f"  Decisions made:     {stats.get('decisions_made', 0)}")

    # Global worker statistics
    print_worker_statistics(worker_stats)

    # Global contract info
    if "global_contract" in results:
        gc = results["global_contract"]
        print("\n" + "-" * 60)
        print("GLOBAL CONTRACT")
        print("-" * 60)
        print(f"  Max warmup bars:    {gc.get('max_warmup_bars', 0)}")
        print(f"  Timeframes:         {', '.join(gc.get('timeframes', []))}")
        print(f"  Total workers:      {gc.get('total_workers', 0)}")

    print("=" * 60)


if __name__ == "__main__":
    """Entry point"""

    # System info
    logger.info(f"System: {platform.system()} {platform.release()}")
    logger.info(f"Python: {platform.python_version()}")
    logger.info(f"CPU Count: {os.cpu_count()}")
    logger.info("=" * 60)

    # Run test
    results = run_strategy_test()

    # Exit with status
    if all(r.get('success', False) for r in results['results']):
        logger.info("✅ All tests passed!")
        exit(0)
    else:
        logger.error("❌ Some tests failed!")
        exit(1)
