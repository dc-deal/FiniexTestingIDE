"""
FiniexTestingIDE - Order Feature Errors (#625)

OrderFeatureRefusedError: a decision logic declares an order feature its venue's API does not
hold, or the pipeline it runs on does not carry. Refused before the run starts, because a level
nothing holds would otherwise read as protection nobody provides.
"""

from python.framework.exceptions.finiex_error import FiniexError


class OrderFeatureRefusedError(FiniexError, ValueError):
    """A decision logic declares an order feature this run cannot provide."""
    pass
