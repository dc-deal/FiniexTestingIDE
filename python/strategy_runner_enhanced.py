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


def run_strategy_test() -> dict:
    """
    Main strategy testing function with detailed statistics output
    """

    logger.info(f"🚀 Starting [BatchOrchestrator] strategy test")

    try:
        # 1. Setup data loader
        loader = TickDataLoader()

        # 2. Create test scenario
        scenario01 = TestScenario(
            symbol="EURUSD",
            start_date="2025-09-25",
            end_date="2025-09-26",
            max_ticks=1000,
            data_mode="realistic",
            # Strategy-Logic (→ Workers)
            strategy_config={
                "rsi_period": 14,
                "envelope_period": 20,
                "envelope_deviation": 0.02,
            },
            # Execution-Optimization (→ Framework)
            execution_config={
                "parallel_workers": True,
                "worker_parallel_threshold_ms": 1.0,
                "max_parallel_scenarios": 4,
                "adaptive_parallelization": True,
                "log_performance_stats": True,
            },
            name=f"EURUSD_01_test",
        )

        # 3. Load from config file
        config_loader = ScenarioConfigLoader()
        loaded_scenarios = config_loader.load_config("eurusd_3_windows.json")

        # 4. Use in BatchOrchestrator
        from python.framework.batch_orchestrator import BatchOrchestrator

        # 3. Create BatchOrchestrator
        orchestrator = BatchOrchestrator([scenario01], loader)

        # 4. Run test (Parallel, Workers.)
        results = orchestrator.run(False, 4)

        # ============================================================
        # STATISTICS EXTRACTION - NEU!
        # ============================================================

        # Get orchestrator statistics (if available)
        if hasattr(orchestrator, "_last_orchestrator"):
            worker_coordinator = orchestrator._last_orchestrator
            if hasattr(worker_coordinator, "get_statistics"):
                stats = worker_coordinator.get_statistics()

                logger.info("=" * 60)
                logger.info("📊 WORKER COORDINATOR STATISTICS")
                logger.info("=" * 60)
                logger.info(
                    f"Ticks processed:       {stats.get('ticks_processed', 0):,}"
                )
                logger.info(
                    f"Worker calls:          {stats.get('worker_calls', 0):,}")
                logger.info(
                    f"Decisions made:        {stats.get('decisions_made', 0):,}"
                )

                # Parallel-specific stats
                if "parallel_execution_time_saved_ms" in stats:
                    time_saved = stats["parallel_execution_time_saved_ms"]
                    avg_saved = stats.get("avg_time_saved_per_tick_ms", 0)

                    logger.info("-" * 60)
                    logger.info("⚡ PARALLELIZATION METRICS")
                    logger.info(f"Total time saved:      {time_saved:.2f}ms")
                    logger.info(f"Avg saved per tick:    {avg_saved:.3f}ms")

                    if time_saved > 0:
                        logger.info("✅ Parallel was FASTER than sequential")
                    elif time_saved < 0:
                        logger.info(
                            "⚠️  Sequential was FASTER (overhead too high)")
                    else:
                        logger.info("➡️  No difference (or parallel disabled)")

                logger.info("=" * 60)

        logger.info("✅ Test completed successfully")
        return results

    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return {"error": str(e), "success": False}


def debug_data_availability():
    """Check data availability"""
    logger.info("🔍 Checking data availability...")

    try:
        loader = TickDataLoader("./data/processed/")
        symbols = loader.list_available_symbols()

        if not symbols:
            logger.error("❌ No symbols found")
            return False

        logger.info(f"✅ Found {len(symbols)} symbols: {symbols}")
        return True

    except Exception as e:
        logger.error(f"❌ Check failed: {e}")
        return False


def clear_terminal():
    """Löscht das Terminal (plattformunabhängig)"""
    os.system("cls" if platform.system() == "Windows" else "clear")


def print_detailed_results(results: dict):
    """
    Print detailed results with statistics

    NEU: Zeigt Worker-Statistiken und Parallelization-Impact
    """
    print("\n" + "=" * 60)
    print("🎉 EXECUTION RESULTS")
    print("=" * 60)

    # Basic results
    print(f"✅ Success:            {results.get('success', True)}")
    print(f"📊 Scenarios:          {results.get('scenarios_count', 0)}")
    print(f"⏱️  Execution time:     {results.get('execution_time', 0):.2f}s")

    if "error" in results:
        print(f"❌ Error:              {results['error']}")
        return

    # Scenario details
    if "results" in results and len(results["results"]) > 0:
        print("\n" + "-" * 60)
        print("SCENARIO DETAILS")
        print("-" * 60)

        for i, scenario_result in enumerate(results["results"], 1):
            print(
                f"\nScenario {i}: {scenario_result.get('scenario_name', 'Unknown')}")
            print(
                f"  Symbol:             {scenario_result.get('symbol', 'N/A')}")
            print(
                f"  Ticks processed:    {scenario_result.get('ticks_processed', 0):,}"
            )
            print(
                f"  Signals generated:  {scenario_result.get('signals_generated', 0)}"
            )
            print(
                f"  Signal rate:        {scenario_result.get('signal_rate', 0):.1%}")

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
    """Main entry point"""

    clear_terminal()
    print("🚀 FiniexTestingIDE Strategy Runner")
    print("=" * 60)

    # Check data
    if not debug_data_availability():
        print("❌ Fix data issues first")
        exit(1)

    # Run test via BatchOrchestrator
    results = run_strategy_test()

    # Display detailed results
    print_detailed_results(results)
