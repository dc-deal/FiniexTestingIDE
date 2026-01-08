"""
FiniexTestingIDE - Environment Check Tests
Pre-flight validation before benchmark execution

Tests:
- Debug mode detection (debugger must NOT be attached)
- Future: Additional environment checks (RAM, CPU throttling, etc.)

IMPORTANT: These tests run BEFORE the actual benchmark to avoid
wasting time on invalid test runs.

Debug mode detection checks:
- sys.gettrace() for attached debuggers
- 'debugpy' module (VS Code debugger)
- 'pydevd' module (PyCharm debugger)
"""

import sys
import pytest


def is_debugger_attached() -> bool:
    """
    Detect if a debugger is currently attached.

    Checks multiple indicators:
    - sys.gettrace(): Set when debugger steps through code
    - debugpy module: VS Code Python debugger
    - pydevd module: PyCharm/IntelliJ debugger

    Returns:
        True if debugger is detected, False otherwise
    """
    return (
        (hasattr(sys, 'gettrace') and sys.gettrace() is not None)
        or 'debugpy' in sys.modules
        or 'pydevd' in sys.modules
    )


class TestEnvironmentCheck:
    """
    Pre-flight environment checks for benchmark validity.

    These tests validate that the execution environment is suitable
    for performance measurement. Debug mode in particular invalidates
    all timing measurements.
    """

    def test_no_debugger_attached(self, request):
        """
        Benchmark must run WITHOUT debugger attached.

        Debuggers add significant overhead that invalidates performance
        measurements. This test ensures the benchmark runs in production
        mode for accurate results.

        If you need to debug the benchmark code:
        1. Debug with this test expected to fail
        2. Fix your issue
        3. Run again WITHOUT debugger for valid measurements
        """
        debugger_active = is_debugger_attached()

        # Store result for report generation (via fixture)
        # This will be picked up by the benchmark_report fixture
        request.config._debug_mode_detected = debugger_active

        if debugger_active:
            # Determine which debugger
            debugger_type = "Unknown"
            if 'debugpy' in sys.modules:
                debugger_type = "VS Code (debugpy)"
            elif 'pydevd' in sys.modules:
                debugger_type = "PyCharm (pydevd)"
            elif hasattr(sys, 'gettrace') and sys.gettrace() is not None:
                debugger_type = "Trace-based debugger"

            pytest.fail(
                f"\n{'='*60}\n"
                f"BENCHMARK INVALID: Debugger Detected\n"
                f"{'='*60}\n\n"
                f"Debugger type: {debugger_type}\n\n"
                f"Performance measurements are invalid when a debugger is attached.\n"
                f"Debuggers add significant overhead that skews timing results.\n\n"
                f"To get valid benchmark results:\n"
                f"1. Stop the debugger\n"
                f"2. Run pytest directly: pytest tests/mvp_benchmark/ -v\n"
                f"3. Or use: python -m pytest tests/mvp_benchmark/ -v\n\n"
                f"Note: Other tests will continue but the report will show FAILED.\n"
                f"{'='*60}"
            )

        print(f"\n✅ Production mode confirmed - no debugger attached")


class TestSystemResources:
    """
    System resource validation.

    Future expansion point for additional checks like:
    - Sufficient free RAM
    - CPU not throttling
    - No heavy background processes
    """

    def test_placeholder_for_future_checks(self):
        """
        Placeholder for future system resource checks.

        Potential future checks:
        - psutil.virtual_memory().percent < 80%
        - CPU frequency at expected level
        - No thermal throttling detected
        """
        # Currently just passes - expand as needed
        pass
