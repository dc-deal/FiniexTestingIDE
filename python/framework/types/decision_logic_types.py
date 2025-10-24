# ============================================
# Trading Decision Structure
# ============================================
from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class Decision:
    """
    Trading decision output from DecisionLogic.

    Replaces dict-based decision format for type safety.
    DecisionLogic returns this structured output to orchestrator.
    """
    action: str  # "BUY", "SELL", "FLAT", "DEFENSIVE", etc.
    confidence: float  # 0.0 - 1.0
    reason: str = ""
    price: float = 0.0
    timestamp: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for logging/serialization"""
        return {
            "action": self.action,
            "confidence": self.confidence,
            "reason": self.reason,
            "price": self.price,
            "timestamp": self.timestamp,
            **self.metadata
        }
