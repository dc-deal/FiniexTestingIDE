"""
Import Pipeline Result Types.

Data structures for parallel bar rendering worker results.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BarRenderResult:
    """
    Result returned by a bar rendering worker subprocess.

    Args:
        symbol: Trading symbol that was rendered
        broker_type: Broker type identifier
        bars_rendered: Total number of bars rendered across all timeframes
        success: Whether rendering completed without errors
        error_message: Error details if success=False
        log_buffer: Collected log lines from the worker process
    """
    symbol: str
    broker_type: str
    bars_rendered: int = 0
    success: bool = True
    error_message: Optional[str] = None
    log_buffer: list[str] = field(default_factory=list)
