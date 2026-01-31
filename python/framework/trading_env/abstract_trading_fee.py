"""
FiniexTestingIDE - Trading Fee Abstract
AbstractTradingFee - Base class for all fees
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from python.framework.types.broker_types import FeeStatus, FeeType


@dataclass
class AbstractTradingFee(ABC):
    """
    Abstract base class for all trading fees.

    All fee types inherit from this and implement calculate_cost().
    Fees are attached to positions and accumulated over the position lifecycle.

    Polymorphic design allows different brokers to use different fee models
    without changing the Position or Portfolio code.
    """
    fee_type: FeeType
    status: FeeStatus
    timestamp: datetime

    # Cost in account currency
    cost: float = 0.0

    # Optional metadata for fee-specific details
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @abstractmethod
    def calculate_cost(self, **kwargs) -> float:
        """
        Calculate fee cost in account currency.

        Implementation varies by fee type.
        Returns absolute cost (always positive, even if swap is credit).
        """
        pass

    def apply(self):
        """Mark fee as applied to balance"""
        self.status = FeeStatus.APPLIED

    def get_display_name(self) -> str:
        """Get human-readable fee name"""
        return self.fee_type.value.replace('_', ' ').title()
