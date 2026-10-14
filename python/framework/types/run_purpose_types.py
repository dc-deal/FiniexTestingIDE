"""
What a run is FOR (#576).

A unit of its own because two layers declare and serve the same set: a configuration declares
it at its top level, both loaders validate it, and the run header, the run index and the API
carry it. Kept here rather than beside the report models so a configuration type does not
import the report types to name it.
"""

from enum import StrEnum


class RunPurpose(StrEnum):
    """
    What a run is FOR — the question the run's other fields leave open.

    Declared by the configuration the run started from (`run_purpose` at its top level, absent
    means REGULAR) and stamped on the header at the start. One field with three values rather
    than switches: a configuration has exactly one purpose, and whether money moved is already
    said by `orders_to`.

    REGULAR: an ordinary run — a backtest, a sweep, a session of a bot
    FIXTURE: a run constructed to show something — every run a test starts, and every run a
        consumer pins; its numbers are built, not earned
    CERTIFICATE: a release-gate run whose record becomes a certificate — never produced again and
        never deleted
    """
    REGULAR = 'regular'
    FIXTURE = 'fixture'
    CERTIFICATE = 'certificate'
