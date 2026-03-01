"""
FiniexTestingIDE - Console Grid Renderer
Generic grid rendering for box layouts

Provides:
- Generic grid rendering (works with any box builder)
- Automatic row/column alignment
- Side-by-side box printing

DRY Principle: Grid logic implemented once, reused for all box types.
"""

from typing import Any, Callable, List
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.utils.console_renderer import ConsoleRenderer


def render_grid(
    batch_summary: BatchExecutionSummary,
    box_creator: Callable[[Any, int, bool], List[str]],
    show_status_line: bool,
    columns: int = 3,
    box_width: int = 38,
    spacing: int = 2
) -> None:
    """
    Render items in grid layout using provided box creator.

    Generic grid renderer - works with ANY box type (scenario, portfolio, etc.)
    Handles row iteration, alignment, and side-by-side printing.

    Args:
        items: List of items to render (ProcessResult, AggregatedPortfolio, etc.)
        box_creator: Function to create box lines for single item
                     Signature: (item, width, show_status) -> List[str]
        renderer: ConsoleRenderer instance (for padding)
        show_status_line: Whether to show status line in boxes
        columns: Number of boxes per row
        box_width: Width of each box (including borders)
        spacing: Number of spaces between boxes
    """
    items = batch_summary.process_result_list
    for i in range(0, len(items), columns):
        row_items = items[i:i+columns]

        # Create box lines for each item in row
        all_boxes = []
        for item in row_items:
            scenario = batch_summary.get_scenario_by_process_result(item)
            box_lines = box_creator(
                item, scenario, box_width, show_status_line)
            all_boxes.append(box_lines)

        # Print boxes side by side
        max_lines = max(len(box) for box in all_boxes)

        for line_idx in range(max_lines):
            line_parts = []
            for box in all_boxes:
                if line_idx < len(box):
                    line_parts.append(box[line_idx])
                else:
                    # Pad missing lines with spaces
                    line_parts.append(' ' * box_width)

            # Join with spacing
            print((" " * spacing).join(line_parts))

        print()  # Empty line between rows


def render_box(
    lines: List[str],
    renderer: ConsoleRenderer,
    box_width: int = 38
) -> List[str]:
    """
    Render symmetric box around content lines.

    Creates box with borders and proper padding.
    Used by box builders to wrap content.

    Args:
        lines: Content lines (may contain ANSI codes)
        renderer: ConsoleRenderer instance (for padding)
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
        padded = renderer.pad_line(line, content_width)
        box_lines.append(f"│ {padded} │")

    # Bottom border
    box_lines.append(f"└{'─' * (box_width - 2)}┘")

    return box_lines
