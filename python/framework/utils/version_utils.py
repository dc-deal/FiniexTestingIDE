"""
Version string utilities.

Dotted version strings ('1.3.0') must never be compared lexicographically —
'1.10.0' < '1.3.0' is True as a string. Parse into integer components instead.
"""

from typing import Optional, Tuple


def parse_version(version: str) -> Optional[Tuple[int, ...]]:
    """
    Parse a dotted version string into comparable integer components.

    Args:
        version: Version string (e.g. '1.3.0'); missing components pad to three

    Returns:
        Tuple of integer components, or None when the string is not a version
    """
    try:
        components = [int(part) for part in version.split('.')]
    except (ValueError, AttributeError):
        return None

    while len(components) < 3:
        components.append(0)

    return tuple(components)
