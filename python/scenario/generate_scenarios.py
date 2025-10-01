"""
FiniexTestingIDE - Scenario Generator CLI
Generates test scenario configs from available data

Usage:
    python python/scenario/generate_scenarios.py
"""

import logging
from pathlib import Path

from python.data_worker.data_loader.core import TickDataLoader
from python.scenario.config_loader import ScenarioConfigLoader
from python.scenario.generator import ScenarioGenerator

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def generate_single_symbol(
    symbol: str,
    strategy_config: dict = None,
    execution_config: dict = None
):
    """
    Generate scenarios for a single symbol

    Example of how to use custom configs
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # ✅ Generate with custom configs
    scenarios = generator.generate_from_symbol(
        symbol,
        strategy="time_windows",
        num_windows=3,
        window_days=2,
        ticks_per_window=1000,

        # Custom Strategy Config (optional)
        strategy_config=strategy_config or {
            "rsi_period": 14,
            "envelope_period": 20,
            "envelope_deviation": 0.02,
        },

        # Custom Execution Config (optional)
        execution_config=execution_config or {
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
    Generate scenarios for multiple symbols

    Demonstrates batch generation
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # ✅ Generate for all symbols (or specific list)
    scenarios = generator.generate_multi_symbol(
        symbols=symbols,  # None = all available
        scenarios_per_symbol=3,
        num_windows=3,
        window_days=2,

        # Shared configs for all scenarios
        strategy_config={
            "rsi_period": 14,
            "envelope_period": 20,
            "envelope_deviation": 0.02,
        },

        execution_config={
            "parallel_workers": True,
            "max_parallel_scenarios": 8,  # More parallel for batch
        }
    )

    # Save
    config_loader = ScenarioConfigLoader()
    output_file = "all_symbols_batch.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated {len(scenarios)} scenarios → {output_file}")


def generate_quick_test():
    """
    Generate a quick test config for debugging

    Fast execution, minimal scenarios
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=1,  # Just one window
        window_days=1,  # One day
        ticks_per_window=100,  # Very few ticks

        strategy_config={
            "rsi_period": 14,
            "envelope_period": 20,
            "envelope_deviation": 0.02,
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
    Generate a heavy batch config for performance testing

    Many scenarios, parallel execution
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=10,  # Many windows
        window_days=2,
        ticks_per_window=1000,

        strategy_config={
            "rsi_period": 14,
            "envelope_period": 20,
            "envelope_deviation": 0.02,
        },

        execution_config={
            "parallel_workers": True,
            "worker_parallel_threshold_ms": 1.0,
            "max_parallel_scenarios": 8,
            "adaptive_parallelization": True,
        }
    )

    config_loader = ScenarioConfigLoader()
    output_file = "heavy_batch.json"
    config_loader.save_config(scenarios, output_file)

    logger.info(f"✅ Generated heavy batch → {output_file}")


if __name__ == "__main__":
    """
    Main entry point - generates various scenario configs
    """
    print("=" * 70)
    print("🚀 FiniexTestingIDE - Scenario Generator")
    print("=" * 70)
    print()

    # Ensure output directory exists
    Path("./configs/scenarios").mkdir(parents=True, exist_ok=True)

    # Generate different types of configs
    try:
        print("📝 Generating quick test config...")
        generate_quick_test()
        print()

        print("📝 Generating single symbol configs...")
        generate_single_symbol("EURUSD")
        generate_single_symbol("GBPUSD")
        generate_single_symbol("AUDUSD")
        print()

        print("📝 Generating heavy batch config...")
        generate_heavy_batch()
        print()

        # Uncomment to generate multi-symbol batch:
        # print("📝 Generating multi-symbol batch...")
        # generate_multi_symbol()
        # print()

        print("=" * 70)
        print("✅ All scenario configs generated successfully!")
        print("📂 Check ./configs/scenarios/ for output files")
        print("=" * 70)

    except Exception as e:
        logger.error(f"❌ Generation failed: {e}", exc_info=True)
