"""
FiniexTestingIDE Blackbox Module
Multi-process hierarchical trading strategy framework
"""

__version__ = "1.0.0"
__author__ = "dc-deal"
__description__ = "Multi-process blackbox framework for trading strategies"

# Core architecture imports
from .multiprocess_architecture import (
    # Base classes
    AbstractBlackboxWorker,
    DecisionOrchestrator,
    BlackboxAdapter,
    
    # Data structures
    TickData,
    WorkerResult,
    WorkerContract,
    WorkerState,
    
    # Concrete worker implementations
    RSIWorker,
    EnvelopeWorker,
)

# Data preparation imports
from .tick_data_preparator import (
    TickDataPreparator,
    quick_prepare_for_testing,
)

# Clean export list
__all__ = [
    # Core Framework
    "AbstractBlackboxWorker",
    "DecisionOrchestrator", 
    "BlackboxAdapter",
    
    # Data Structures
    "TickData",
    "WorkerResult", 
    "WorkerContract",
    "WorkerState",
    
    # Pre-built Workers
    "RSIWorker",
    "EnvelopeWorker",
    
    # Data Preparation
    "TickDataPreparator",
    "quick_prepare_for_testing",
    
    # Module Info
    "__version__",
    "__description__",
]