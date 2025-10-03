"""
FiniexTestingIDE - Scenario Generator CLI
Generates test scenario configs from available data

REFACTORED (Issue 2): Now generates factory-compatible config structure
with decision_logic_type, worker_types, and explicit worker configs.

Usage:
    python python/scenario/generate_scenarios.py
"""

import logging
from pathlib import Path

from python.data_worker.data_loader.core import TickDataLoader
from python.scenario.config_loader import ScenarioConfigLoader
from python.scenario.generator import ScenarioGenerator
from python.components.logger.bootstrap_logger import setup_logging

setup_logging(name="StrategyRunner")
logger = logging.getLogger(__name__)


def generate_single_symbol(
    symbol: str,
    decision_logic_type: str = "CORE/simple_consensus",
    worker_types: list = None,
    workers_config: dict = None,
):
    """
    Generate scenarios for a single symbol.

    REFACTORED (Issue 2): Uses new config structure with explicit
    decision logic type and worker configuration.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Default workers if not specified
    if worker_types is None:
        worker_types = ["CORE/rsi", "CORE/envelope"]

    # Default worker configs if not specified
    if workers_config is None:
        workers_config = {
            "CORE/rsi": {
                "period": 14,
                "timeframe": "M5"
            },
            "CORE/envelope": {
                "period": 20,
                "deviation": 0.02,
                "timeframe": "M5"
            }
        }

    # Generate scenarios with new structure
    scenarios = generator.generate_from_symbol(
        symbol,
        strategy="time_windows",
        num_windows=3,
        window_days=2,
        ticks_per_window=1000,

        # ============================================
        # NEW (Issue 2): Factory-compatible parameters
        # ============================================
        decision_logic_type=decision_logic_type,
        worker_types=worker_types,
        workers_config=workers_config,

        # Optional: DecisionLogic-specific config
        decision_logic_config={
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "min_confidence": 0.6
        },

        # Execution settings (unchanged)
        execution_config={
            "parallel_workers": True,
            "worker_parallel_threshold_ms": 1.0,
            "max_parallel_scenarios": 4,
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        }
    )

    # Save to config file
    config_loader = ScenarioConfigLoader()
    output_file = f"{symbol}_3_windows.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated {len(scenarios)} scenarios → {output_file}")


def generate_multi_symbol(symbols: list = None):
    """
    Generate scenarios for multiple symbols.

    REFACTORED (Issue 2): Uses new config structure.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Generate for all symbols (or specific list)
    scenarios = generator.generate_multi_symbol(
        symbols=symbols,  # None = all available
        scenarios_per_symbol=3,

        # ============================================
        # NEW (Issue 2): Factory-compatible parameters
        # ============================================
        decision_logic_type="CORE/simple_consensus",
        worker_types=["CORE/rsi", "CORE/envelope"],
        workers_config={
            "CORE/rsi": {"period": 14, "timeframe": "M5"},
            "CORE/envelope": {"period": 20, "deviation": 0.02, "timeframe": "M5"}
        },

        execution_config={
            "parallel_workers": True,
            "max_parallel_scenarios": 8,
        }
    )

    # Save
    config_loader = ScenarioConfigLoader()
    output_file = "all_symbols_batch.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated {len(scenarios)} scenarios → {output_file}")


def generate_quick_test():
    """
    Generate a quick test config for debugging.

    REFACTORED (Issue 2): Uses new config structure.
    Fast execution, minimal scenarios, sequential mode.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=1,  # Just one window
        window_days=1,  # One day
        ticks_per_window=100,  # Very few ticks

        # ============================================
        # NEW (Issue 2): Factory-compatible parameters
        # ============================================
        decision_logic_type="CORE/simple_consensus",
        worker_types=["CORE/rsi", "CORE/envelope"],
        workers_config={
            "CORE/rsi": {"period": 14, "timeframe": "M5"},
            "CORE/envelope": {"period": 20, "deviation": 0.02, "timeframe": "M5"}
        },

        execution_config={
            "parallel_workers": False,  # Sequential for debugging
            "max_parallel_scenarios": 1,
            "log_performance_stats": True,
        }
    )

    config_loader = ScenarioConfigLoader()
    output_file = "quick_test.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated quick test → {output_file}")


def generate_heavy_batch():
    """
    Generate a heavy batch config for performance testing.

    REFACTORED (Issue 2): Now uses Heavy Workers with artificial load.

    This configuration is designed for stress-testing parallelization:
    - Heavy Workers simulate CPU-intensive computations (ML, FFT, etc.)
    - artificial_load_ms parameter adds controllable CPU load per worker
    - Tests parallel worker execution under realistic compute constraints
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # ============================================
    # NEW (Issue 2): Heavy Workers for Performance Testing
    # ============================================
    # These workers have artificial_load_ms to simulate heavy computation
    heavy_worker_types = ["CORE/heavy_rsi",
                          "CORE/heavy_envelope", "CORE/heavy_macd"]

    heavy_workers_config = {
        "CORE/heavy_rsi": {
            "period": 14,
            "timeframe": "M5",
            "artificial_load_ms": 5.0,  # 5ms CPU load
        },
        "CORE/heavy_envelope": {
            "period": 20,
            "deviation": 0.02,
            "timeframe": "M5",
            "artificial_load_ms": 8.0,  # 8ms CPU load (heavier)
        },
        "CORE/heavy_macd": {
            "fast": 12,
            "slow": 26,
            "signal": 9,
            "timeframe": "M5",
            "artificial_load_ms": 6.0,  # 6ms CPU load
        }
    }

    scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=4,  # Multiple windows for stress test
        window_days=2,
        ticks_per_window=1000,

        # Heavy Workers configuration
        decision_logic_type="CORE/simple_consensus",  # Same logic, heavy workers
        worker_types=heavy_worker_types,
        workers_config=heavy_workers_config,

        # Aggressive parallelization for performance testing
        execution_config={
            "parallel_workers": True,  # Enable parallel workers
            "worker_parallel_threshold_ms": 1.0,
            "max_parallel_scenarios": 8,  # High parallelization
            "adaptive_parallelization": True,
            "log_performance_stats": True,  # Track performance metrics
        }
    )

    config_loader = ScenarioConfigLoader()
    output_file = "heavy_batch.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated heavy batch (3 heavy workers) → {output_file}")


def generate_custom_strategy_example():
    """
    Example: Generate scenarios with custom worker configuration.

    Shows how to override default worker parameters per scenario.
    This demonstrates the parameter inheritance system.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Custom worker config with different RSI period
    custom_workers = {
        "CORE/rsi": {
            "period": 21,  # Longer period RSI
            "timeframe": "M5"
        },
        "CORE/envelope": {
            "period": 30,  # Wider envelope
            "deviation": 0.03,
            "timeframe": "M5"
        }
    }

    scenarios = generator.generate_from_symbol(
        "GBPUSD",
        strategy="time_windows",
        num_windows=3,

        decision_logic_type="CORE/simple_consensus",
        worker_types=["CORE/rsi", "CORE/envelope"],
        workers_config=custom_workers,  # Custom config

        decision_logic_config={
            "rsi_oversold": 25,  # More aggressive thresholds
            "rsi_overbought": 75,
        }
    )

    config_loader = ScenarioConfigLoader()
    output_file = "GBPUSD_custom_strategy.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated custom strategy → {output_file}")


if __name__ == "__main__":
    """
    Main entry point - generates various scenario configs.

    REFACTORED (Issue 2): All generators now use factory-compatible
    config structure with explicit decision logic and worker types.
    """
    logger.info("=" * 70)
    logger.info("🚀 FiniexTestingIDE - Scenario Generator (Issue 2)")
    logger.info("=" * 70)

    # Ensure output directory exists
    Path("./configs/scenarios").mkdir(parents=True, exist_ok=True)

    # Generate different types of configs
    try:
        logger.info("📝 Generating quick test config...")
        generate_quick_test()

        logger.info("📝 Generating single symbol configs...")
        generate_single_symbol("EURUSD")
        generate_single_symbol("GBPUSD")
        generate_single_symbol("AUDUSD")

        logger.info("📝 Generating heavy batch config (performance test)...")
        generate_heavy_batch()

        logger.info("📝 Generating custom strategy example...")
        generate_custom_strategy_example()

        # Uncomment to generate multi-symbol batch:
        # logger.info("📝 Generating multi-symbol batch...")
        # generate_multi_symbol()

        logger.info("=" * 70)
        logger.info("✅ All scenario configs generated successfully!")
        logger.info("📂 Check ./configs/scenarios/ for output files")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"❌ Generation failed: {e}", exc_info=True)
