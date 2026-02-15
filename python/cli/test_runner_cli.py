"""
FiniexTestingIDE - Test Runner CLI
Runs all test suites (except benchmark) and saves output to logs/tests/

Usage:
    python python/cli/test_runner_cli.py
    python python/cli/test_runner_cli.py --include-benchmark
"""

import io
import os
import sys
from datetime import datetime


def main():
    # pytest must be imported here (not at top) to avoid collection issues
    import pytest

    include_benchmark = "--include-benchmark" in sys.argv

    # Build pytest args
    pytest_args = [
        "tests/",
        "-v",
        "--tb=short",
    ]

    if not include_benchmark:
        pytest_args.append("--ignore=tests/mvp_benchmark/")

    # Prepare log directory and filename
    log_dir = os.path.join(os.path.dirname(__file__), "..", "..", "logs", "tests")
    os.makedirs(log_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_filename = f"test_all_{timestamp}.log"
    log_path = os.path.join(log_dir, log_filename)

    # Capture output to both console and file
    class TeeWriter:
        """Writes to both original stream and a string buffer."""
        def __init__(self, original):
            self.original = original
            self.buffer = io.StringIO()

        def write(self, text):
            self.original.write(text)
            self.buffer.write(text)

        def flush(self):
            self.original.flush()

        def getvalue(self):
            return self.buffer.getvalue()

    tee_out = TeeWriter(sys.stdout)
    tee_err = TeeWriter(sys.stderr)

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = tee_out, tee_err

    try:
        exit_code = pytest.main(pytest_args)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    # Write combined output to log file
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(tee_out.getvalue())
        err_output = tee_err.getvalue()
        if err_output:
            f.write(err_output)

    print(f"\n{'='*60}")
    print(f"Log saved: {os.path.relpath(log_path)}")
    print(f"{'='*60}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
