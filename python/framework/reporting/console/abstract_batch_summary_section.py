"""
FiniexTestingIDE - Abstract Batch Summary Section
Base class for all batch summary section renderers
"""

from abc import ABC

from python.framework.utils.console_renderer import ConsoleRenderer


class AbstractBatchSummarySection(ABC):
    """
    Base class for batch summary sections.

    Provides unified section header rendering for all summary components.
    """

    _section_title: str = ''

    def _render_section_header(self, renderer: ConsoleRenderer) -> None:
        """
        Render section header with separators.

        Args:
            renderer: ConsoleRenderer instance
        """
        renderer.section_separator()
        renderer.print_bold(self._section_title)
        renderer.section_separator()
