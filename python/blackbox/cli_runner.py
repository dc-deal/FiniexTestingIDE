#!/usr/bin/env python3
"""
FiniexTestingIDE - CLI Test Runner
Command-line interface for running strategy tests
"""

import argparse
import sys
import json
from pathlib import Path
import logging
from datetime import datetime

from python.blackbox.batch_orchestrator import BatchOrchestrator
from python.blackbox.types import TestScenario
from python.data_loader.core import TickDataLoader

# Setup logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="FiniexTestingIDE - Strategy Testing CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic test with default parameters
  python run_test.py --symbol EURUSD --max-ticks 1000

  # Custom parameters
  python run_test.py --symbol GBPUSD --rsi-period 21 --data-mode clean

  # Multiple symbols with output directory
  python run_test.py --symbol EURUSD GBPUSD --output results/test_001/
        """,
    )

    # Data parameters
    parser.add_argument(
        "--symbol",
        "-s",
        type=str,
        nargs="+",
        default=["EURUSD"],
        help="Trading symbol(s) to test (default: EURUSD)",
    )

    parser.add_argument(
        "--max-ticks",
        "-t",
        type=int,
        default=1000,
        help="Maximum ticks to process (default: 1000)",
    )

    parser.add_argument(
        "--data-mode",
        "-m",
        choices=["clean", "realistic", "raw"],
        default="realistic",
        help="Data quality mode (default: realistic)",
    )

    parser.add_argument(
        "--start-date", type=str, help="Start date (ISO format: 2024-01-01)"
    )

    parser.add_argument(
        "--end-date", type=str, help="End date (ISO format: 2024-12-31)"
    )

    # Strategy parameters
    parser.add_argument(
        "--rsi-period", type=int, default=14, help="RSI period (default: 14)"
    )

    parser.add_argument(
        "--envelope-period", type=int, default=20, help="Envelope period (default: 20)"
    )

    parser.add_argument(
        "--envelope-deviation",
        type=float,
        default=0.02,
        help="Envelope deviation (default: 0.02)",
    )

    # Output parameters
    parser.add_argument("--output", "-o", type=str, help="Output directory for results")

    parser.add_argument("--json", action="store_true", help="Export results as JSON")

    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    return parser.parse_args()


def check_data_availability(loader: TickDataLoader, symbols: list) -> bool:
    """Check if data is available for requested symbols"""
    available = loader.list_available_symbols()

    if not available:
        logger.error("No data found. Run tick_importer.py first.")
        return False

    missing = [s for s in symbols if s not in available]
    if missing:
        logger.error(f"Symbols not found: {missing}")
        logger.info(f"Available symbols: {available}")
        return False

    return True


def create_scenarios(args) -> list:
    """Create test scenarios from arguments"""
    scenarios = []

    strategy_config = {
        "rsi_period": args.rsi_period,
        "envelope_period": args.envelope_period,
        "envelope_deviation": args.envelope_deviation,
    }

    for symbol in args.symbol:
        scenario = TestScenario(
            symbol=symbol,
            start_date=args.start_date or "2024-01-01",
            end_date=args.end_date or "2024-12-31",
            max_ticks=args.max_ticks,
            data_mode=args.data_mode,
            strategy_config=strategy_config,
            name=f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        )
        scenarios.append(scenario)

    return scenarios


def print_results(results: dict):
    """Print test results to console"""
    print("\n" + "=" * 70)
    print("TEST RESULTS SUMMARY")
    print("=" * 70)

    if not results.get("success"):
        print(f"\nError: {results.get('error', 'Unknown error')}")
        return

    print(f"\nScenarios executed: {results.get('scenarios_count', 0)}")
    print(f"Total execution time: {results.get('execution_time', 0):.2f}s")

    # Global contract info
    if "global_contract" in results:
        contract = results["global_contract"]
        print(f"\nStrategy Configuration:")
        print(f"  Workers: {contract.get('total_workers', 0)}")
        print(f"  Timeframes: {', '.join(contract.get('timeframes', []))}")
        print(f"  Warmup bars: {contract.get('max_warmup_bars', 0)}")

    # Individual scenario results
    if "results" in results:
        print("\n" + "-" * 70)
        for i, scenario_result in enumerate(results["results"], 1):
            print(f"\nScenario {i}: {scenario_result.get('scenario_name', 'Unknown')}")
            print(f"  Symbol: {scenario_result.get('symbol', 'N/A')}")
            print(f"  Ticks processed: {scenario_result.get('ticks_processed', 0):,}")
            print(f"  Signals generated: {scenario_result.get('signals_generated', 0)}")
            print(f"  Signal rate: {scenario_result.get('signal_rate', 0):.2%}")

            # Show first few signals
            if scenario_result.get("signals"):
                print(f"\n  First signals:")
                for signal in scenario_result["signals"][:3]:
                    print(
                        f"    {signal.get('action')} @ {signal.get('price', 0):.5f} "
                        f"(confidence: {signal.get('confidence', 0):.2f})"
                    )

    print("\n" + "=" * 70)


def export_json(results: dict, output_dir: Path):
    """Export results as JSON"""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = output_dir / f"test_results_{timestamp}.json"

    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(f"Results exported to: {output_file}")


def main():
    """Main CLI entry point"""
    args = parse_arguments()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    print("\n" + "=" * 70)
    print("FiniexTestingIDE - Strategy Test Runner")
    print("=" * 70)

    try:
        # Initialize data loader
        loader = TickDataLoader("./data/processed/")

        # Check data availability
        if not check_data_availability(loader, args.symbol):
            sys.exit(1)

        # Create test scenarios
        scenarios = create_scenarios(args)
        logger.info(f"Created {len(scenarios)} test scenario(s)")

        # Display test configuration
        print("\nTest Configuration:")
        print(f"  Symbols: {', '.join(args.symbol)}")
        print(f"  Max ticks: {args.max_ticks:,}")
        print(f"  Data mode: {args.data_mode}")
        print(f"  RSI period: {args.rsi_period}")
        print(
            f"  Envelope: period={args.envelope_period}, deviation={args.envelope_deviation}"
        )

        # Run tests
        print("\nRunning tests...")
        orchestrator = BatchOrchestrator(scenarios, loader)
        results = orchestrator.run()

        # Print results
        print_results(results)

        # Export if requested
        if args.json or args.output:
            output_dir = Path(args.output) if args.output else Path("results")
            export_json(results, output_dir)

        # Exit code based on success
        sys.exit(0 if results.get("success") else 1)

    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(130)

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=args.verbose)
        sys.exit(1)


if __name__ == "__main__":
    main()
