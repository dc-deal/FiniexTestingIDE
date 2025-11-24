"""
FiniexTestingIDE - Console Renderer
Unified console rendering with proper box formatting

Provides:
- Symmetric box rendering (fixed alignment bug)
- Color utilities (ANSI codes)
- Grid layouts for scenarios and portfolio
- Formatted text output

"""

import re
from typing import List
from python.framework.reporting.console_box_renderer import ConsoleBoxRenderer
from python.framework.types.currency_codes import format_currency_simple
from python.framework.types.log_level import ColorCodes
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.rendering_types import BoxRenderConfig
from python.framework.utils.time_utils import format_duration, format_tick_timespan


class ConsoleRenderer:
    """
    Unified console renderer for all summary outputs.

    Handles:
    - Box rendering with perfect symmetry
    - Color formatting
    - Grid layouts
    - Section separators
    """

    def __init__(self):
        """Initialize console renderer."""
        self._box_renderer = ConsoleBoxRenderer(self, BoxRenderConfig())

    # ============================================
    # Color Utilities
    # ============================================

    def red(self, text: str) -> str:
        """Color text red."""
        return f"{ColorCodes.RED}{text}{ColorCodes.RESET}"

    def yellow(self, text: str) -> str:
        """Color text yellow."""
        return f"{ColorCodes.YELLOW}{text}{ColorCodes.RESET}"

    def blue(self, text: str) -> str:
        """Color text blue."""
        return f"{ColorCodes.BLUE}{text}{ColorCodes.RESET}"

    def green(self, text: str) -> str:
        """Color text green."""
        return f"{ColorCodes.GREEN}{text}{ColorCodes.RESET}"

    def gray(self, text: str) -> str:
        """Color text gray."""
        return f"{ColorCodes.GRAY}{text}{ColorCodes.RESET}"

    def bold(self, text: str) -> str:
        """Make text bold."""
        return f"{ColorCodes.BOLD}{text}{ColorCodes.RESET}"

    # ============================================
    # Section Formatting
    # ============================================

    def section_header(self, title: str, width: int = 60, char: str = "="):
        """Print section header."""
        print(f"\n{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{title.center(width)}{ColorCodes.RESET}")
        print(f"{ColorCodes.BOLD}{char * width}{ColorCodes.RESET}")

    def section_separator(self, width: int = 60, char: str = "-"):
        """Print section separator."""
        print(f"{char * width}")

    def print_separator(self, width: int = 60, char: str = "-"):
        """Print simple separator line."""
        print(char * width)

    def print_bold(self, text: str):
        """Print bold text."""
        print(self.bold(text))

    # ============================================
    # String Utilities (ANSI-aware)
    # ============================================

    def strip_ansi(self, text: str) -> str:
        """
        Remove ANSI color codes from string.

        Args:
            text: String potentially containing ANSI codes

        Returns:
            String without ANSI codes
        """
        ansi_escape = re.compile(r'\033\[[0-9;]*m')
        return ansi_escape.sub('', text)

    def visual_length(self, text: str) -> int:
        """
        Get visual length of string (excluding ANSI codes).

        Args:
            text: String to measure

        Returns:
            Visual length (what user sees)
        """
        return len(self.strip_ansi(text))

    def pad_line(self, text: str, width: int) -> str:
        """
        Pad line to exact visual width.

        Args:
            text: String to pad (may contain ANSI codes)
            width: Target visual width

        Returns:
            Padded string with exact visual width
        """
        visual_len = self.visual_length(text)

        if visual_len > width:
            # Truncate if too long (preserve ANSI codes at start)
            stripped = self.strip_ansi(text)
            return text[:len(text) - len(stripped) + width]

        # Add padding
        padding = ' ' * (width - visual_len)
        return text + padding

    # ============================================
    # Box Rendering (Symmetric)
    # ============================================

    def render_box(self, lines: List[str], box_width: int = 38) -> List[str]:
        """
        Render symmetric box around lines.

        Args:
            lines: Lines of text (may contain ANSI codes)
            box_width: Total box width (including borders)

        Returns:
            List of box lines ready to print
        """
        content_width = box_width - 4  # Account for "│ " and " │"

        box_lines = []

        # Top border
        box_lines.append(f"┌{'─' * (box_width - 2)}┐")

        # Content lines
        for line in lines:
            padded = self.pad_line(line, content_width)
            box_lines.append(f"│ {padded} │")

        # Bottom border
        box_lines.append(f"└{'─' * (box_width - 2)}┘")

        return box_lines

    # ============================================
    # Grid Rendering
    # ============================================

    def render_scenario_grid(self, scenarios: List[ProcessResult], columns: int = 3, box_width: int = 38):
        """
        Render scenarios in grid layout.

        Args:
            scenarios: List of Scenario objects
            columns: Number of columns in grid
            box_width: Width of each box
        """
        for i in range(0, len(scenarios), columns):
            row_scenarios = scenarios[i:i+columns]

            # Create lines for each box
            all_boxes = []

            for scenario in row_scenarios:
                box_lines = self._box_renderer.create_scenario_box(
                    scenario, box_width)
                all_boxes.append(box_lines)

            # Print boxes side by side
            max_lines = max(len(box) for box in all_boxes)

            for line_idx in range(max_lines):
                line_parts = []
                for box in all_boxes:
                    if line_idx < len(box):
                        line_parts.append(box[line_idx])
                    else:
                        line_parts.append(' ' * box_width)

                print("  ".join(line_parts))

            print()  # Empty line between rows

    def render_portfolio_grid(self,  scenarios: List[ProcessResult], columns: int = 3, box_width: int = 38):
        """
        Render portfolio stats in grid layout.

        Args:
            scenarios: List of scenario result dicts with portfolio stats
            columns: Number of columns in grid
            box_width: Width of each box
        """
        for i in range(0, len(scenarios), columns):
            row_scenarios = scenarios[i:i+columns]

            # Create lines for each box
            all_boxes = []

            for scenario in row_scenarios:
                box_lines = self._box_renderer.create_portfolio_box(
                    scenario, box_width)
                all_boxes.append(box_lines)

            # Print boxes side by side
            max_lines = max(len(box) for box in all_boxes)

            for line_idx in range(max_lines):
                line_parts = []
                for box in all_boxes:
                    if line_idx < len(box):
                        line_parts.append(box[line_idx])
                    else:
                        line_parts.append(' ' * box_width)

                print("  ".join(line_parts))

            print()  # Empty line between rows
