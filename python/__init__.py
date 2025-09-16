"""
FiniexTestingIDE Python Package
Core trading strategy testing framework
"""

__version__ = "0.1.0"
__author__ = "dc-deal"
__description__ = "Professional Trading Strategy Testing & Development Environment"

# Core imports
from .blackbox_framework import BlackboxBase, Signal, Tick, Parameter
from .data_loader import TickDataLoader
from .tick_importer import TickDataImporter

__all__ = [
    "BlackboxBase",
    "Signal", 
    "Tick",
    "Parameter",
    "TickDataLoader",
    "TickDataImporter"
]