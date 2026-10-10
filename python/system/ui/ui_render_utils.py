"""
FiniexTestingIDE - UI Render Utils
Small text pieces the console screens share.
"""

PROGRESS_FILLED = '█'
PROGRESS_EMPTY = '░'

# The sizes a screen shows: one definition, so two lines of one screen never disagree.
BYTES_PER_MB = 1024 ** 2
BYTES_PER_GB = 1024 ** 3


def progress_bar(fraction: float, width: int = 20) -> str:
    """
    A text progress bar.

    Args:
        fraction: Share done, clamped to 0..1
        width: Characters in the bar

    Returns:
        The bar, e.g. '██████░░░░' for 0.6 at width 10
    """
    share = min(max(fraction, 0.0), 1.0)
    filled = int(round(share * width))
    return PROGRESS_FILLED * filled + PROGRESS_EMPTY * (width - filled)


def format_size(size_bytes: int) -> str:
    """
    A file size in MB, or in GB from one GB.

    Args:
        size_bytes: The size

    Returns:
        The size with its unit, e.g. '392.9 MB'
    """
    if size_bytes >= BYTES_PER_GB:
        return f'{size_bytes / BYTES_PER_GB:.1f} GB'
    return f'{size_bytes / BYTES_PER_MB:.1f} MB'
