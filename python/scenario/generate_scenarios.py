"""
FiniexTestingIDE - Scenario Generator CLI
Generates test scenario configs from available data

REFACTORED (Worker Instance System): Now generates configs with
worker_instances dict (instance_name → worker_type) and workers
dict indexed by instance names.

NEW (C#003 Refactor):
- All generator functions support trade_simulator_config
- Added examples for custom balance/currency configurations
- Added advanced example for per-scenario balance variations

Usage:
    python python/scenario/generate_scenarios.py
"""

import traceback
from pathlib import Path

from python.data_worker.data_loader.core import TickDataLoader
from python.scenario.config_saver import ScenarioConfigSaver
from python.scenario.generator import ScenarioGenerator

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()

ts_config_global = {
    "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
    "initial_balance": 10000,
    "currency": "EUR"
}


def generate_single_symbol(
    symbol: str,
    decision_logic_type: str = "CORE/aggressive_trend",
    worker_instances: dict = None,
    workers_config: dict = None,
    trade_simulator_config: dict = None,
):
    """
    Generate scenarios for a single symbol.

    REFACTORED (Worker Instance System): Uses worker_instances dict
    with instance names as keys.

    NEW (C#003): Added optional trade_simulator_config parameter.

    Args:
        symbol: Trading symbol (e.g., "EURUSD")
        decision_logic_type: DecisionLogic type to use
        worker_instances: Dict[instance_name, worker_type]
        workers_config: Worker configs indexed by instance name
        trade_simulator_config: TradeSimulator config (optional)
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Default worker instances if not specified
    if worker_instances is None:
        worker_instances = {
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        }

    # Default worker configs if not specified
    if workers_config is None:
        workers_config = {
            "rsi_fast": {
                "period": 14,
                "timeframe": "M5"
            },
            "envelope_main": {
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

        # Worker instance system
        decision_logic_type=decision_logic_type,
        worker_instances=worker_instances,
        workers_config=workers_config,

        # Optional: DecisionLogic-specific config
        decision_logic_config={
            "rsi_oversold": 30,
            "rsi_overbought": 70,
            "min_confidence": 0.6
        },

        # Execution settings
        execution_config={
            "parallel_workers": True,
            "worker_parallel_threshold_ms": 1.0,
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        },

        # NEW (C#003): TradeSimulator config (optional)
        trade_simulator_config=trade_simulator_config or ts_config_global
    )

    # Save to config file
    config_saver = ScenarioConfigSaver()
    output_file = f"{symbol}_3_windows.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated {len(scenarios)} scenarios → {output_file}")


def generate_multi_symbol(
    symbols: list = None,
    trade_simulator_config: dict = None
):
    """
    Generate scenarios for multiple symbols.

    REFACTORED (Worker Instance System): Uses worker_instances dict.
    NEW (C#003): Added optional trade_simulator_config parameter.

    Args:
        symbols: List of symbols (None = all available)
        trade_simulator_config: TradeSimulator config (optional)
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Generate for all symbols (or specific list)
    scenarios = generator.generate_multi_symbol(
        symbols=symbols,  # None = all available
        scenarios_per_symbol=3,

        # Worker instance system
        decision_logic_type="CORE/aggressive_trend",
        worker_instances={
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        },
        workers_config={
            "rsi_fast": {"period": 14, "timeframe": "M5"},
            "envelope_main": {"period": 20, "deviation": 0.02, "timeframe": "M5"}
        },

        execution_config={
            "parallel_workers": True,
        },

        # NEW (C#003): TradeSimulator config (optional)
        trade_simulator_config=trade_simulator_config or ts_config_global
    )

    # Save
    config_saver = ScenarioConfigSaver()
    output_file = "all_symbols_batch.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated {len(scenarios)} scenarios → {output_file}")


def generate_quick_test():
    """
    Generate a quick test config for debugging.

    REFACTORED (Worker Instance System): Uses worker_instances dict.
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

        # Worker instance system
        decision_logic_type="CORE/aggressive_trend",
        worker_instances={
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        },
        workers_config={
            "rsi_fast": {"period": 14, "timeframe": "M5"},
            "envelope_main": {"period": 20, "deviation": 0.02, "timeframe": "M5"}
        },

        execution_config={
            "parallel_workers": False,  # Sequential for debugging
            "log_performance_stats": True,
        },

        trade_simulator_config=ts_config_global
    )

    config_saver = ScenarioConfigSaver()
    output_file = "quick_test.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated quick test → {output_file}")


def generate_heavy_batch():
    """
    Generate a heavy batch config for performance testing.

    REFACTORED (Worker Instance System): Uses heavy worker instances.

    This configuration is designed for stress-testing parallelization:
    - Heavy Workers simulate CPU-intensive computations (ML, FFT, etc.)
    - artificial_load_ms parameter adds controllable CPU load per worker
    - Tests parallel worker execution under realistic compute constraints
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Heavy worker instances for performance testing
    heavy_worker_instances = {
        "rsi_heavy": "CORE/heavy_rsi",
        "envelope_heavy": "CORE/heavy_envelope",
        "macd_heavy": "CORE/heavy_macd"
    }

    heavy_workers_config = {
        "rsi_heavy": {
            "period": 14,
            "timeframe": "M5",
            "artificial_load_ms": 5.0,  # 5ms CPU load
        },
        "envelope_heavy": {
            "period": 20,
            "deviation": 0.02,
            "timeframe": "M5",
            "artificial_load_ms": 8.0,  # 8ms CPU load (heavier)
        },
        "macd_heavy": {
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

        # Heavy worker instances
        decision_logic_type="CORE/aggressive_trend",
        worker_instances=heavy_worker_instances,
        workers_config=heavy_workers_config,

        # Aggressive parallelization for performance testing
        execution_config={
            "parallel_workers": True,
            "worker_parallel_threshold_ms": 1.0,
            "adaptive_parallelization": True,
            "log_performance_stats": True,
        },

        trade_simulator_config=ts_config_global
    )

    config_saver = ScenarioConfigSaver()
    output_file = "heavy_batch.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated heavy batch (3 heavy workers) → {output_file}")


def generate_custom_strategy_example():
    """
    Example: Generate scenarios with custom worker configuration.

    Shows how to override default worker parameters per scenario.
    This demonstrates the parameter inheritance system.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Custom worker instances with descriptive names
    custom_worker_instances = {
        "rsi_slow": "CORE/rsi",        # Longer period RSI
        "envelope_wide": "CORE/envelope"  # Wider envelope
    }

    # Custom worker config with different parameters
    custom_workers_config = {
        "rsi_slow": {
            "period": 21,  # Longer period RSI
            "timeframe": "M5"
        },
        "envelope_wide": {
            "period": 30,  # Wider envelope
            "deviation": 0.03,
            "timeframe": "M5"
        }
    }

    scenarios = generator.generate_from_symbol(
        "GBPUSD",
        strategy="time_windows",
        num_windows=3,

        decision_logic_type="CORE/aggressive_trend",
        worker_instances=custom_worker_instances,
        workers_config=custom_workers_config,

        decision_logic_config={
            "rsi_oversold": 25,  # More aggressive thresholds
            "rsi_overbought": 75,
        },

        trade_simulator_config=ts_config_global
    )

    config_saver = ScenarioConfigSaver()
    output_file = "GBPUSD_custom_strategy.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated custom strategy → {output_file}")


def generate_dual_rsi_strategy():
    """
    Example: Strategy with multiple instances of same worker type.

    Demonstrates:
    - Multiple RSI workers with different parameters
    - Descriptive instance names (rsi_fast, rsi_slow)
    - How DecisionLogic can use multiple instances for comparison
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Multiple RSI instances with different timeframes
    dual_rsi_instances = {
        "rsi_fast": "CORE/rsi",    # M1 fast RSI
        "rsi_slow": "CORE/rsi",    # M5 slow RSI
        "envelope_main": "CORE/envelope"
    }

    dual_rsi_config = {
        "rsi_fast": {
            "period": 14,
            "timeframe": "M1"  # Fast timeframe
        },
        "rsi_slow": {
            "period": 14,
            "timeframe": "M5"  # Slow timeframe
        },
        "envelope_main": {
            "period": 20,
            "deviation": 0.02,
            "timeframe": "M5"
        }
    }

    scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=3,

        decision_logic_type="CORE/aggressive_trend",
        worker_instances=dual_rsi_instances,
        workers_config=dual_rsi_config,

        trade_simulator_config=ts_config_global
    )

    config_saver = ScenarioConfigSaver()
    output_file = "eurusd_dual_rsi.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated dual RSI strategy → {output_file}")


# ============================================
# NEW (C#003): TradeSimulator Config Examples
# ============================================

def generate_with_custom_balance():
    """
    NEW (C#003): Generate scenarios with custom TradeSimulator config.

    Demonstrates:
    - Setting different initial balance for all scenarios (5k instead of 10k)
    - Changing currency (USD instead of EUR)
    - Using specific broker config

    Use case: Testing strategy with smaller/larger account sizes.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Custom balance and currency
    custom_ts_config = {
        "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
        "initial_balance": 5000,
        "currency": "USD"
    }

    scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=3,
        window_days=2,
        ticks_per_window=1000,

        # Worker instances
        decision_logic_type="CORE/aggressive_trend",
        worker_instances={
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        },
        workers_config={
            "rsi_fast": {"period": 14, "timeframe": "M5"},
            "envelope_main": {"period": 20, "deviation": 0.02, "timeframe": "M5"}
        },

        # Custom TradeSimulator config
        trade_simulator_config=custom_ts_config
    )

    config_saver = ScenarioConfigSaver()
    output_file = "eurusd_custom_balance.json"
    config_saver.save_config(scenarios, output_file)

    vLog.info(f"✅ Generated scenarios with $5k USD balance → {output_file}")


def generate_per_scenario_balance():
    """
    NEW (C#003): Advanced example - Different balance per scenario.

    Shows how to manually create scenarios with different
    TradeSimulator configs for each scenario.

    Use case: Testing strategy performance across different
    account sizes (1k, 5k, 10k, 50k) to find optimal starting capital.
    """
    loader = TickDataLoader("./data/processed/")
    generator = ScenarioGenerator(loader)

    # Generate base scenarios with standard config
    base_scenarios = generator.generate_from_symbol(
        "EURUSD",
        strategy="time_windows",
        num_windows=4,  # 4 scenarios for 4 different balances
        window_days=2,
        ticks_per_window=1000,

        decision_logic_type="CORE/aggressive_trend",
        worker_instances={
            "rsi_fast": "CORE/rsi",
            "envelope_main": "CORE/envelope"
        },
        workers_config={
            "rsi_fast": {"period": 14, "timeframe": "M5"},
            "envelope_main": {"period": 20, "deviation": 0.02, "timeframe": "M5"}
        },

        trade_simulator_config=ts_config_global
    )

    # Manually assign different balances to each scenario
    balances = [1000.0, 5000.0, 10000.0, 50000.0]

    for i, scenario in enumerate(base_scenarios):
        # Override trade_simulator_config for each scenario
        scenario.trade_simulator_config = {
            **ts_config_global,
            "initial_balance": balances[i]
        }
        # Update scenario name to reflect balance
        scenario.name = f"EURUSD_balance_{int(balances[i])}"

    config_saver = ScenarioConfigSaver()
    output_file = "eurusd_multi_balance.json"
    config_saver.save_config(base_scenarios, output_file)

    vLog.info(
        f"✅ Generated scenarios with varied balances (1k-50k) → {output_file}")


# ============================================
# Main Entry Point
# ============================================

if __name__ == "__main__":
    """
    Main entry point - generates various scenario configs.

    REFACTORED (Worker Instance System): All generators now use
    worker_instances dict with instance names as keys.

    NEW (C#003): Added examples for custom TradeSimulator configurations.
    """
    vLog.info("=" * 70)
    vLog.info("🚀 FiniexTestingIDE - Scenario Generator")
    vLog.info("=" * 70)

    # Ensure output directory exists
    Path("./configs/scenario_sets").mkdir(parents=True, exist_ok=True)

    # Generate different types of configs
    try:
        vLog.info("📝 Generating quick test config...")
        generate_quick_test()

        vLog.info("📝 Generating single symbol configs (default settings)...")
        generate_single_symbol("EURUSD")
        generate_single_symbol("GBPUSD")
        generate_single_symbol("AUDUSD")

        vLog.info("📝 Generating heavy batch config (performance test)...")
        generate_heavy_batch()

        vLog.info("📝 Generating custom strategy example...")
        generate_custom_strategy_example()

        vLog.info("📝 Generating dual RSI strategy (multiple instances)...")
        generate_dual_rsi_strategy()

        # NEW (C#003): TradeSimulator config examples
        vLog.info("📝 Generating with custom balance (5k USD)...")
        generate_with_custom_balance()

        vLog.info("📝 Generating with varied balances per scenario (1k-50k)...")
        generate_per_scenario_balance()

        # Uncomment to generate multi-symbol batch:
        # vLog.info("📝 Generating multi-symbol batch...")
        # generate_multi_symbol()

        vLog.info("=" * 70)
        vLog.info("✅ All scenario configs generated successfully!")
        vLog.info("📂 Check ./configs/scenario_sets/ for output files")
        vLog.info("=" * 70)

    except Exception as e:
        vLog.error(f"❌ Generation failed: \n{traceback.format_exc()}")
