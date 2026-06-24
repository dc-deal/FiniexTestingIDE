"""
FiniexTestingIDE - Swap Errors

SwapModeNotImplementedError (#407): a symbol's broker config declares a swap_mode the
swap engine does not model (only POINTS computes, NONE = no swap). Raised at the
AutoTrader startup gate (single session → abort, §35). The batch pipeline converts the
same condition into a per-scenario ValidationResult (config/data error, §33) instead of
raising, so one bad symbol never blocks the other scenarios.
"""

from python.framework.exceptions.finiex_error import FiniexError
from python.framework.types.trading_env_types.broker_types import SwapMode


class SwapModeNotImplementedError(FiniexError, ValueError):
    """A symbol uses a swap_mode the engine does not model (supported: points, none)."""

    def __init__(self, symbol: str, swap_mode: SwapMode):
        self.symbol = symbol
        self.swap_mode = swap_mode
        super().__init__(
            f"Symbol '{symbol}' uses swap_mode '{swap_mode.value}' which the swap engine "
            f"does not model. Supported modes: 'points', 'none'."
        )
