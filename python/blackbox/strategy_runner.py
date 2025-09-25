"""
FiniexTestingIDE - Strategy Runner
Universal entry point using BatchOrchestrator for all tests
"""

import logging
from pathlib import Path

from python.blackbox.batch_orchestrator import BatchOrchestrator
from python.blackbox.types import TestScenario
from python.data_loader import TickDataLoader

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_strategy_test(
    symbol: str = "EURUSD",
    max_ticks: int = 1000,
    data_mode: str = "realistic",
    start_date: str = None,
    end_date: str = None,
) -> dict:
    """
    Main strategy testing function - ALWAYS uses BatchOrchestrator

    Args:
        symbol: Trading symbol
        max_ticks: Maximum ticks to process
        data_mode: Data quality mode
        start_date: Start date (optional)
        end_date: End date (optional)
    """

    logger.info(f"🚀 Starting strategy test for {symbol}")

    try:
        # 1. Setup data loader
        loader = TickDataLoader("./data/processed/")

        # 2. Create test scenario
        scenario = TestScenario(
            symbol=symbol,
            start_date=start_date or "2024-01-01",
            end_date=end_date or "2024-12-31",
            max_ticks=max_ticks,
            data_mode=data_mode,
            strategy_config={
                "rsi_period": 14,
                "envelope_period": 20,
                "envelope_deviation": 0.02,
            },
            name=f"{symbol}_test",
        )

        # 3. Create BatchOrchestrator (universal entry point)
        orchestrator = BatchOrchestrator([scenario], loader)

        # 4. Run (works for 1 or 1000 scenarios)
        results = orchestrator.run()

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


if __name__ == "__main__":
    """Main entry point"""

    print("🚀 FiniexTestingIDE Strategy Runner")
    print("=" * 60)

    # Check data
    if not debug_data_availability():
        print("❌ Fix data issues first")
        exit(1)

    # Run test via BatchOrchestrator
    results = run_strategy_test(symbol="EURUSD", max_ticks=100, data_mode="realistic")

    # Display results
    print("\n" + "=" * 60)
    print("🎉 RESULTS:")
    print(f"Success: {results.get('success', True)}")
    print(f"Scenarios: {results.get('scenarios_count', 0)}")
    print(f"Execution time: {results.get('execution_time', 0):.2f}s")

    if "error" in results:
        print(f"❌ Error: {results['error']}")

    print("=" * 60)
