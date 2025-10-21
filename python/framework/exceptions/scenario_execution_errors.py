"""
FiniexTestingIDE - Scenario Execution Errors
Exception types for scenario execution failures
"""


class ScenarioPreparationError(Exception):
    """
    Raised when scenario preparation (warmup phase) fails.

    This includes failures in:
    - Data loading
    - Worker creation
    - Trade simulator setup
    - Bar rendering preparation

    Scenarios that fail preparation are excluded from execution.
    """
    pass


class ScenarioExecutionError(Exception):
    """
    Raised when scenario tick loop execution fails.

    This includes failures during:
    - Tick processing
    - Signal generation
    - Order execution
    - Statistics collection

    Execution errors are logged but do not stop other scenarios.
    """
    pass


class ScenarioStateError(Exception):
    """
    Raised when execute_tick_loop() is called without prior prepare_scenario().

    ScenarioExecutor requires two-phase execution:
    1. prepare_scenario() - warmup and setup
    2. execute_tick_loop() - actual tick processing

    Calling execute_tick_loop() without preparation is a programming error.
    """
    pass
