"""
FiniexTestingIDE - Console Box Renderer
Thin wrapper for scenario and portfolio box rendering

This class now serves as a facade for backward compatibility.
"""

from typing import List
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.batch_reporting.grid.console_grid_renderer import render_grid
from python.framework.batch_reporting.grid.scenario_box_builder import create_scenario_box
from python.framework.batch_reporting.grid.portfolio_box_builder import create_portfolio_box
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.rendering_types import BoxRenderConfig


class ConsoleBoxRenderer:
    """
    Facade for box rendering functionality.
    """

    def __init__(self, renderer: ConsoleRenderer, config: BoxRenderConfig = None):
        """
        Initialize box renderer.

        Args:
            renderer: ConsoleRenderer instance (for colors, padding, box borders)
            config: Box render configuration (uses defaults if None)
        """
        self._renderer = renderer
        self._config = config or BoxRenderConfig()

    def render_scenario_grid(
        self,
        batch_summary: BatchExecutionSummary,
        show_status_line: bool = False,
        columns: int = 3,
        box_width: int = 38
    ):
        """
        Render process_results in grid layout.

        Args:
            process_results: List of ProcessResult objects
            show_status_line: Whether to show status line in boxes
            columns: Number of columns in grid
            box_width: Width of each box
        """
        render_grid(
            batch_summary=batch_summary,
            box_creator=create_scenario_box,
            show_status_line=show_status_line,
            columns=columns,
            box_width=box_width
        )

    def render_portfolio_grid(
        self,
        batch_summary: BatchExecutionSummary,
        show_status_line: bool = False,
        columns: int = 3,
        box_width: int = 38
    ):
        """
        Render portfolio stats in grid layout.

        Args:
            process_results: List of ProcessResult objects with portfolio stats
            show_status_line: Whether to show status line in boxes
            columns: Number of columns in grid
            box_width: Width of each box
        """
        render_grid(
            batch_summary=batch_summary,
            box_creator=create_portfolio_box,
            show_status_line=show_status_line,
            columns=columns,
            box_width=box_width
        )
