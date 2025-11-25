"""
FiniexTestingIDE - Rendering Types
Type definitions for console rendering configuration
"""

from dataclasses import dataclass
from enum import Enum


class BatchStatus(Enum):
    """
    Batch execution status across all scenarios.

    Used for conditional rendering and color coding in summary.
    """
    SUCCESS = "success"   # All scenarios successful
    PARTIAL = "partial"   # Some scenarios failed
    FAILED = "failed"     # All scenarios failed


@dataclass
class BoxRenderConfig:
    """
    Configuration for box rendering in console output.

    Controls box dimensions and grid layout for scenario/portfolio displays.

    Attributes:
        box_width: Total box width including borders
        columns: Number of boxes per row in grid layout
        scenario_lines: Content lines for scenario statistics box
        portfolio_lines: Content lines for portfolio statistics box
        column_spacing: Spaces between adjacent boxes in grid
    """
    box_width: int = 38           # Total box width including borders
    columns: int = 3              # Boxes per row in grid
    scenario_lines: int = 9       # Content lines for scenario box
    portfolio_lines: int = 11     # Content lines for portfolio box
    column_spacing: int = 2       # Spaces between boxes in grid
