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
from python.framework.types.currency_codes import format_currency_simple
from python.framework.types.log_level import ColorCodes


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

    def pnl(self, input: float, currency: str):
        """Return colorized P&L string based on value."""
        amount_str = format_currency_simple(input, currency)

        if input > 0:
            return self.green(f"+{amount_str}")
        elif input < 0:
            return self.red(f"-{amount_str}")  # ← MINUS HINZUFÜGEN!
        else:
            return amount_str

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
