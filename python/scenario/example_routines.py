"""
FiniexTestingIDE - Scenario Config System
Examples
"""

import logging

from python.data_worker.data_loader.core import TickDataLoader
from python.scenario.config_loader import ScenarioConfigLoader
from python.scenario.generator import ScenarioGenerator

logger = logging.getLogger(__name__)


def example_usage(symbol: str):
    """Example of how to use the config system"""

    # 1. Generate scenarios automatically
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Generate 3 time windows for EURUSD
    scenarios = generator.generate_from_symbol(
        symbol,
        strategy="time_windows",
        num_windows=3,
        window_days=2,
        ticks_per_window=1000
    )

    # 2. Save to config file
    config_loader = ScenarioConfigLoader()
    config_loader.save_config(
        scenarios, f"./{symbol}_3_windows.json")


def example_multi_symbol():
    """Generate scenarios for all available symbols"""

    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Generate for all symbols (3 scenarios each)
    scenarios = generator.generate_multi_symbol(
        symbols=None,  # All available
        scenarios_per_symbol=3,
        num_windows=3,
        window_days=2
    )

    # Save
    config_loader = ScenarioConfigLoader()
    config_loader.save_config(scenarios, "all_symbols_batch.json")

    logger.info(f"Generated config with {len(scenarios)} scenarios")


if __name__ == "__main__":
    """Main entry point for examples"""

    print("🚀 FiniexTestingIDE Scenario generator Examples")
    print("=" * 60)

    # runs
    print("Run Examplary usage routine")
    example_usage("AUDUSD")
    example_usage("EURGBP")
    example_usage("EURUSD")
    # print("Run example multi usage")
    # example_multi_symbol()
