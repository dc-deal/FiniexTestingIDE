"""
FiniexTestingIDE - Index Origin Summary

The lines the index status prints about whose data an archive holds (#647): for a symbol's bars,
the origin classes they were rendered from and where our own collection takes over; for its tick
files, how many files and ticks carry each origin class. Formatting only — the figures are derived
by the index managers.
"""

from typing import List

from python.framework.types.data_origin_types import OriginFileCount, SymbolDataOrigin


def print_bar_origin(origin: SymbolDataOrigin, indent: str) -> None:
    """
    Print a symbol's bar origin: the classes, and the seam where there is one.

    Args:
        origin: Whose data the symbol's bars were rendered from
        indent: The indentation of the status block the lines belong to
    """
    classes = ', '.join(origin_class.value for origin_class in origin.origin_classes)
    print(f'{indent}Origin:     {classes or "not stamped"}')
    if origin.collected_since is not None:
        print(f'{indent}Collected since: {origin.collected_since:%Y-%m-%d %H:%M:%S} UTC'
              f' (a venue\'s published history before it)')


def print_tick_origin(counts: List[OriginFileCount]) -> None:
    """
    Print a symbol's tick files per origin class.

    Args:
        counts: Files and ticks per origin class, in the order to print them
    """
    for count in counts:
        print(f'Origin:      {count.origin_class:<12} {count.files:,} file(s) · {count.ticks:,} ticks')
