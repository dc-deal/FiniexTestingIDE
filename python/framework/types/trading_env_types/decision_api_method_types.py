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
