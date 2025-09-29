from typing import Dict, List, Any, Optional, Tuple, Set
from collections import defaultdict, deque
from datetime import datetime, timedelta
import pandas as pd
import time

"""
FiniexTestingIDE - Strategy Runner
Universal entry point using BatchOrchestrator for all tests
"""

import logging
from pathlib import Path

from python.blackbox.batch_orchestrator import BatchOrchestrator
from python.blackbox.decision_orchestrator import DecisionOrchestrator
from python.blackbox.tick_data_preparator import TickDataPreparator
from python.blackbox.bar_rendering_orchestrator import BarRenderingOrchestrator
from python.blackbox.blackbox_adapter import BlackboxAdapter
from python.blackbox.workers import RSIWorker, EnvelopeWorker
from python.blackbox.types import TestScenario, TickData, Bar, TimeframeConfig
from python.data_loader.core import TickDataLoader

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

    logger.info(f"🚀 Starting [BatchOrchestrator] strategy test for {symbol}")

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


def create_rsi_envelope_strategy(
    rsi_period: int = 14, envelope_period: int = 20, envelope_deviation: float = 0.02
) -> BlackboxAdapter:
    """
    Factory function to create RSI+Envelope strategy

    🔍 DEBUG POINT: Set breakpoint here to inspect strategy creation
    """
    logger.info(
        f"Creating RSI+Envelope strategy (RSI: {rsi_period}, Envelope: {envelope_period})"
    )

    # 🔍 DEBUG POINT: Check worker configuration
    rsi_worker = RSIWorker(period=rsi_period)
    envelope_worker = EnvelopeWorker(
        period=envelope_period, deviation=envelope_deviation
    )

    # 🔍 DEBUG POINT: Inspect worker contracts
    rsi_contract = rsi_worker.get_contract()
    envelope_contract = envelope_worker.get_contract()
    logger.debug(f"RSI contract: {rsi_contract}")
    logger.debug(f"Envelope contract: {envelope_contract}")

    # Create orchestrator
    orchestrator = DecisionOrchestrator([rsi_worker, envelope_worker])

    # Create adapter
    adapter = BlackboxAdapter(orchestrator)

    return adapter


def debug_data_availability():
    """
    Debug helper: Check what data is available

    🔍 DEBUG HELPER: Call this first to check your data setup
    """

    logger.info("🔍 Debugging data availability...")

    try:
        loader = TickDataLoader("./data/processed/")
        symbols = loader.list_available_symbols()

        if not symbols:
            logger.error("❌ No symbols found in ./data/processed/")
            logger.info("💡 Run: python python/tick_importer.py")
            return False

        logger.info(f"✅ Found {len(symbols)} symbols: {symbols}")

        # Check first symbol
        test_symbol = symbols[0]
        info = loader.get_symbol_info(test_symbol)
        logger.info(f"📊 {test_symbol} info: {info}")

        return True

    except Exception as e:
        logger.error(f"❌ Data availability check failed: {e}")
        return False
