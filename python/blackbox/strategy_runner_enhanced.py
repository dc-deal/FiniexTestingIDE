from typing import Dict, List, Any, Optional, Tuple,Set  
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
from python.blackbox.workers import RSIWorker,EnvelopeWorker
from python.blackbox.types import TestScenario,TickData,Bar, TimeframeConfig
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


def run_strategy_test(
    symbol: str = "EURUSD", max_ticks: int = 1000, data_mode: str = "realistic"
) -> Dict[str, Any]:
    """
    Main strategy testing function - ENHANCED with Bar Rendering

    🔍 MAIN DEBUG ENTRY POINT: Set breakpoint on this line to start debugging
    """

    logger.info(f"🚀 Starting strategy test for {symbol}")
    logger.info(f"Parameters: max_ticks={max_ticks}, data_mode={data_mode}")

    results = {
        "symbol": symbol,
        "signals_generated": 0,
        "ticks_processed": 0,
        "performance_stats": {},
        "errors": [],
    }

    # NEW: Bar Rendering Orchestrator
    bar_orchestrator = None

    try:
        # === STEP 1: DATA PREPARATION ===
        # 🔍 DEBUG POINT: Check if your data loader works
        logger.info("Step 1: Loading data...")
        loader = TickDataLoader("./data/processed/")
        available_symbols = loader.list_available_symbols()

        if not available_symbols:
            raise ValueError("❌ No symbols found! Run tick_importer.py first")

        if symbol not in available_symbols:
            logger.warning(f"Symbol {symbol} not found. Available: {available_symbols}")
            symbol = available_symbols[0]  # Use first available
            logger.info(f"Using {symbol} instead")

        # 🔍 DEBUG POINT: Inspect symbol info
        preparator = TickDataPreparator(loader)
        symbol_info = preparator.get_symbol_info(symbol)
        logger.info(
            f"Symbol info: {symbol_info.get('total_ticks', 'unknown')} ticks available"
        )

        # === STEP 2: STRATEGY CREATION ===
        # 🔍 DEBUG POINT: Strategy initialization
        logger.info("Step 2: Creating strategy...")
        adapter = create_rsi_envelope_strategy(
            rsi_period=14, envelope_period=20, envelope_deviation=0.02
        )

        # 🔍 DEBUG POINT: Check contract aggregation
        logger.info("Step 3: Initializing strategy...")
        contract = adapter.initialize()
        logger.info(f"Strategy contract: {contract}")
        results["contract_info"] = contract

        # === NEW STEP: BAR RENDERING SETUP ===
        logger.info("Step 3.5: Setting up bar rendering system...")
        bar_orchestrator = BarRenderingOrchestrator(loader)

        # Register workers for bar analysis
        workers = list(
            adapter.orchestrator.workers.values()
        )  # Get actual worker objects

        if workers:
            bar_orchestrator.register_workers(workers)
            logger.info(f"✓ Bar rendering system ready with {len(workers)} workers")
        else:
            logger.warning("No workers found for bar rendering")

        # === STEP 3: DATA PREPARATION ===
        # 🔍 DEBUG POINT: Warmup data preparation
        logger.info("Step 4: Preparing data...")
        warmup_bars_needed = contract.get("min_warmup_bars", 50)

        warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
            symbol=symbol,
            warmup_bars_needed=warmup_bars_needed,
            test_ticks_count=max_ticks,
            data_mode=data_mode,
        )

        # 🔍 DEBUG POINT: Check warmup data
        logger.info(f"Warmup: {len(warmup_ticks)} ticks")
        if warmup_ticks:
            logger.debug(f"First warmup tick: {warmup_ticks[0]}")
            logger.debug(f"Last warmup tick: {warmup_ticks[-1]}")

        # === STEP 4: WARMUP PHASE - Bar warmup only ===
        logger.info("Step 5: Preparing bar warmup...")

        if bar_orchestrator and warmup_ticks:
            # Determine test start time from first warmup tick
            first_test_tick_time = pd.to_datetime(warmup_ticks[-1].timestamp)
            bar_orchestrator.prepare_warmup(symbol, first_test_tick_time)

            # Get warmup bars for orchestrator
            warmup_bars = {}
            for tf in bar_orchestrator._required_timeframes:
                warmup_bars[tf] = bar_orchestrator.get_warmup_bars(tf)

            logger.info(
                f"Bar warmup complete: {sum(len(bars) for bars in warmup_bars.values())} bars"
            )
        else:
            logger.warning("No bar warmup available")

        # === STEP 5: LIVE PROCESSING WITH BAR RENDERING ===
        # 🔍 MAIN PROCESSING LOOP DEBUG POINT: Set breakpoint here
        logger.info("Step 6: Processing live ticks with bar rendering...")

        tick_count = 0
        signals = []
        start_time = time.time()

        for tick in test_iterator:
            # 🔍 DEBUG POINT: Individual tick processing
            # Uncomment next line for detailed tick debugging:
            # logger.debug(f"Processing tick {tick_count}: {tick.timestamp} @ {tick.mid:.5f}")

            # NEW: Process tick through bar rendering system FIRST
            if bar_orchestrator:
                current_bars = bar_orchestrator.process_tick(tick)

                # Log bar updates for debugging
                if tick_count % 100 == 0 and current_bars:
                    for tf, bar in current_bars.items():
                        logger.debug(
                            f"Current {tf} bar: O:{bar.open:.5f} H:{bar.high:.5f} L:{bar.low:.5f} C:{bar.close:.5f}"
                        )

            # Original decision processing WITH BAR DATA
            decision = adapter.process_tick(tick, current_bars)
            tick_count += 1

            # 🔍 DEBUG POINT: Signal generation
            if decision and decision["action"] != "FLAT":
                signals.append(decision)
                logger.info(
                    f"🎯 SIGNAL #{len(signals)}: {decision['action']} @ {tick.mid:.5f} "
                    f"(confidence: {decision['confidence']:.2f}, reason: {decision['reason']})"
                )

                # 🔍 DEBUG POINT: Detailed signal inspection
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f"Signal metadata: {decision.get('metadata', {})}")

            # Progress logging
            if tick_count % 100 == 0:
                elapsed = time.time() - start_time
                rate = tick_count / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Progress: {tick_count}/{max_ticks} ticks, {len(signals)} signals, "
                    f"{rate:.1f} ticks/sec"
                )

        # === STEP 6: RESULTS COLLECTION ===
        # 🔍 DEBUG POINT: Final results
        processing_time = time.time() - start_time
        performance_stats = adapter.get_performance_stats()

        logger.info("Step 7: Collecting results...")
        logger.info(f"✅ Test completed in {processing_time:.2f}s")
        logger.info(
            f"✅ Processed {tick_count} ticks, generated {len(signals)} signals"
        )
        logger.info(f"✅ Signal rate: {len(signals)/tick_count:.2%}")
        logger.info(f"✅ Performance: {performance_stats}")

        # NEW: Bar rendering statistics
        if bar_orchestrator:
            logger.info("✅ Bar rendering statistics:")
            for tf in bar_orchestrator._required_timeframes:
                current_bar = bar_orchestrator.get_current_bar(symbol, tf)
                history_count = len(bar_orchestrator.get_bar_history(symbol, tf))
                logger.info(
                    f"  {tf}: {history_count} completed bars, current: {current_bar.close if current_bar else 'None'}"
                )

        # Update results
        results.update(
            {
                "signals_generated": len(signals),
                "ticks_processed": tick_count,
                "signal_rate": len(signals) / tick_count if tick_count > 0 else 0,
                "processing_time_seconds": processing_time,
                "processing_rate_ticks_per_sec": (
                    tick_count / processing_time if processing_time > 0 else 0
                ),
                "performance_stats": performance_stats,
                "signals": signals[:10],  # First 10 signals for inspection
                "success": True,
            }
        )

    except Exception as e:
        # 🔍 DEBUG POINT: Error handling
        logger.error(f"❌ Strategy test failed: {e}", exc_info=True)
        results["errors"].append(str(e))
        results["success"] = False

    finally:
        # 🔍 DEBUG POINT: Cleanup
        logger.info("Step 8: Cleaning up...")
        try:
            adapter.cleanup()
        except Exception as e:
            logger.warning(f"Cleanup warning: {e}")

    return results


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


def debug_minimal_test():
    """
    Minimal test for step-by-step debugging

    🔍 MINIMAL DEBUG: Use this for focused debugging
    """

    logger.info("🔍 Running minimal debug test...")

    # Just test data loading
    loader = TickDataLoader("./data/processed/")
    symbols = loader.list_available_symbols()

    if not symbols:
        logger.error("No data found")
        return

    # Test preparator
    preparator = TickDataPreparator(loader)
    warmup, test_iter = preparator.prepare_test_and_warmup_split(
        symbol=symbols[0], warmup_bars_needed=10, test_ticks_count=5
    )

    logger.info(f"Warmup: {len(warmup)} ticks")
    test_ticks = list(test_iter)
    logger.info(f"Test: {len(test_ticks)} ticks")

    if test_ticks:
        logger.info(f"Sample tick: {test_ticks[0]}")

    # NEW: Test bar rendering
    logger.info("🔍 Testing bar rendering...")
    bar_orchestrator = BarRenderingOrchestrator(loader)

    # Mock workers for testing
    class MockWorker:
        def __init__(self, name):
            self.name = name
            self.required_timeframes = ["M1", "M5"]

    mock_workers = [MockWorker("TestWorker")]
    bar_orchestrator.register_workers(mock_workers)

    # Test tick processing
    if test_ticks:
        current_bars = bar_orchestrator.process_tick(test_ticks[0])
        logger.info(f"Bar rendering test: {len(current_bars)} bars updated")


if __name__ == "__main__":
    """
    🔍 MAIN DEBUG ENTRY POINTS:

    1. Set breakpoint on debug_data_availability() - Check data setup
    2. Set breakpoint on debug_minimal_test() - Minimal test
    3. Set breakpoint on run_strategy_test() - Full strategy test
    """

    print("🚀 FiniexTestingIDE Strategy Runner - Enhanced with Bar Rendering")
    print("=" * 60)

    # 🔍 DEBUG STEP 1: Check data availability first
    print("Step 1: Checking data availability...")
    if not debug_data_availability():
        print("❌ Data check failed. Fix data issues before proceeding.")
        exit(1)

    print("\nStep 2: Running minimal test...")
    debug_minimal_test()

    print("\nStep 3: Running full strategy test...")
    # 🔍 MAIN DEBUG POINT: Set breakpoint on next line
    results = run_strategy_test(
        symbol="EURUSD",  # Change to available symbol
        max_ticks=100,  # Small number for testing
        data_mode="realistic",
    )

    # 🔍 DEBUG POINT: Inspect final results
    print("\n" + "=" * 60)
    print("🎉 FINAL RESULTS:")
    print(f"Success: {results.get('success', False)}")
    print(f"Signals generated: {results.get('signals_generated', 0)}")
    print(f"Ticks processed: {results.get('ticks_processed', 0)}")
    print(f"Signal rate: {results.get('signal_rate', 0):.2%}")

    if results.get("errors"):
        print(f"❌ Errors: {results['errors']}")

    print("=" * 60)
