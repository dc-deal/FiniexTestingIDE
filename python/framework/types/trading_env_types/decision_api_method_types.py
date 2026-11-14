"""
FiniexTestingIDE - Decision API Method Types
What each public method of the decision trading API does to the run (#653).
"""

from enum import StrEnum


class DecisionApiMethodKind(StrEnum):
    """
    Whether a public method of the decision trading API reads what a decision can see, or acts.

    A READS method hands the decision something of the run's state; whatever it hands out must be
    the same whether the portfolio marks its positions lazily or keeps them current, which the
    observer probe checks by reading every READS method on every pass. An ACTS method changes the
    run — an order, a close, a request — and is exercised by the probe's plan instead.
    """
    READS = 'reads'
    ACTS = 'acts'


class DecisionApiReturnKind(StrEnum):
    """
    What a READS method of the decision trading API hands the decision (#654) — never the engine's
    own object, which the algo could change for everyone and through which an unreviewed field
    would reach it.

    MASK: a read-only view of an engine object, its declared fields current at every read
    FROZEN: an immutable value the engine replaces rather than changes
    COPY: a value built for this one call, which nothing else holds
    VALUE: a number, a flag, a text, an enum, a time — or None
    """
    MASK = 'mask'
    FROZEN = 'frozen'
    COPY = 'copy'
    VALUE = 'value'
