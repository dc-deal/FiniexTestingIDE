"""
Core Framework Workers
Standard indicator implementations provided by FiniexTestingIDE
"""

from .rsi_worker import RSIWorker
from .envelope_worker import EnvelopeWorker
from .heavy_workers import (
    HeavyEnvelopeWorker, HeavyMACDWorker, HeavyRSIWorker)

__all__ = ['RSIWorker', 'EnvelopeWorker',
           "HeavyEnvelopeWorker", "HeavyMACDWorker", "HeavyRSIWorker"]
