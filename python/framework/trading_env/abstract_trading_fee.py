"""
FiniexTestingIDE - Trading Fee Abstract
AbstractTradingFee - Base class for all fees
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from python.framework.types.trading_env_types.broker_types import FeeStatus, FeeType


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

    def apply_reported_cost(self, reported_cost: float) -> None:
        """
        Replace the computed cost with the one the venue reported for this fill (#508).

        The fee keeps its type and its category — a maker/taker fee stays one with its maker
        flag unchanged — so every reader that sorts costs by kind keeps working. What the model
        computed stays readable in `metadata['estimated_cost']`: the gap between the two is the
        model's error, which is the reason the venue's figure is the one worth booking. The same
        call serves every model that charges per fill, a percentage or a commission per lot.

        Args:
            reported_cost: The venue's fee for the volume this fee covers, in the same currency
                the model computes in
        """
        self.metadata['estimated_cost'] = self.cost
        self.cost = abs(reported_cost)

