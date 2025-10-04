"""
FiniexTestingIDE - TUI Live Monitor Demo
Demonstrates how to use TUIAdapter for live monitoring

NEW (V0.7): Demo for future TUI implementation

This script shows how a future TUI would poll the TUIAdapter
for live performance metrics during execution.

Usage:
    python python/demos/tui_live_monitor_demo.py
"""

import time
import json
from python.framework.monitoring import TUIAdapter, TUIMetricsFormatter


def demo_live_monitoring_simulation():
    """
    Simulate live monitoring with TUI adapter.

    This demonstrates the polling pattern that would be used
    by a real TUI implementation.
    """
    print("=" * 80)
    print("TUI ADAPTER DEMO - Live Monitoring Simulation")
    print("=" * 80)
    print()

    # Create adapter (would be connected to real orchestrator in production)
    adapter = TUIAdapter()

    print("📊 Simulating live metrics polling (every 300ms)...")
    print("(In production, this would show real-time execution data)")
    print()

    # Simulate 10 polling cycles
    for i in range(10):
        # Get live metrics (this would be real data in production)
        metrics = adapter.get_live_metrics()

        # Display formatted output
        print(f"Cycle {i+1}:")
        print(f"  Status: {metrics['status']}")
        print(f"  Scenario: {metrics['scenario']}")
        print(f"  Ticks: {metrics['progress']['ticks_processed']}")
        print(f"  Workers active: {len(metrics['workers'])}")
        print(f"  Decisions made: {metrics['decision_logic']['decisions']}")
        print()

        # Wait for next cycle (300ms)
        time.sleep(0.3)

    print("Demo complete! In production, this would continue until execution finishes.")
    print()


def demo_metrics_formatting():
    """
    Demonstrate metrics formatting for terminal display.
    """
    print("=" * 80)
    print("METRICS FORMATTING DEMO")
    print("=" * 80)
    print()

    # Sample worker data
    sample_workers = [
        {
            "name": "RSI_M5",
            "type": "CORE/rsi",
            "calls": 1000,
            "avg_ms": 0.451,
            "min_ms": 0.2,
            "max_ms": 2.1,
        },
        {
            "name": "Envelope_M5",
            "type": "CORE/envelope",
            "calls": 1000,
            "avg_ms": 0.389,
            "min_ms": 0.1,
            "max_ms": 1.8,
        },
    ]

    # Format as ASCII table
    print("Worker Performance Table:")
    print()
    table = TUIMetricsFormatter.format_worker_table(sample_workers)
    print(table)
    print()

    # Format progress bar
    print("Progress Bar Examples:")
    print()
    print("50% complete:")
    print(TUIMetricsFormatter.format_progress_bar(500, 1000))
    print()
    print("Unknown total:")
    print(TUIMetricsFormatter.format_progress_bar(750, None))
    print()


def demo_json_export():
    """
    Demonstrate JSON export of metrics (for logging/analysis).
    """
    print("=" * 80)
    print("JSON EXPORT DEMO")
    print("=" * 80)
    print()

    adapter = TUIAdapter()
    metrics = adapter.get_live_metrics()

    print("Metrics as JSON (for logging/analysis):")
    print()
    print(json.dumps(metrics, indent=2))
    print()


if __name__ == "__main__":
    """Run all demos"""

    print("\n")
    print("╔" + "═" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "  FiniexTestingIDE - TUI Adapter Demonstrations".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "═" * 78 + "╝")
    print()

    # Run demos
    try:
        demo_live_monitoring_simulation()
        time.sleep(1)

        demo_metrics_formatting()
        time.sleep(1)

        demo_json_export()

        print()
        print("=" * 80)
        print("✅ All demos completed successfully!")
        print("=" * 80)
        print()
        print("📝 Next Steps:")
        print("   1. Integrate TUIAdapter with BatchOrchestrator")
        print("   2. Build actual TUI using 'rich' or 'textual' library")
        print("   3. Add real-time charts and visualizations")
        print()

    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        import traceback
        traceback.print_exc()
