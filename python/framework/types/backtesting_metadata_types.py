"""
FiniexTestingIDE - Backtesting Metadata Types
Type definitions for backtesting validation data

MVP Validation Testing:
- Warmup validation errors
- Bar snapshots at specific ticks
- Expected trades from deterministic sequence
- Tick count tracking

Data Flow:
1. BacktestingSampleWorker collects warmup_status + bar_snapshots
2. BacktestingDeterministic extracts data and builds BacktestingMetadata
3. get_statistics() returns DecisionLogicStats with backtesting_metadata
4. Test Suite validates against prerendered data + calculated delays

IMPORTANT: All data must be JSON-serializable (no Bar objects directly).
Bar snapshots stored as dicts via Bar.to_dict().
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class BacktestingMetadata:
    """
    Validation data collected during backtesting decision logic execution.
    
    This dataclass aggregates all validation-relevant data from:
    - Worker: warmup validation, bar snapshots
    - Decision Logic: expected trades, tick count
    
    All fields are JSON-serializable for cross-process transfer.
    
    Attributes:
        warmup_errors: List of warmup validation errors (empty = all valid)
        bar_snapshots: Dict of bar snapshots captured at specific ticks
                       Key format: "{timeframe}_bar{index}_tick{tick_number}"
                       Value: Bar dict from Bar.to_dict()
        expected_trades: List of expected trades from deterministic sequence
                        Each dict contains: signal_tick, direction, lot_size
        tick_count: Total ticks processed by decision logic
    
    Example bar_snapshots:
        {
            "M5_bar3_tick150": {
                "timestamp": "2025-10-09T20:15:00+00:00",
                "open": 1.32950,
                "high": 1.32980,
                "low": 1.32940,
                "close": 1.32975,
                "volume": 0.0,
                "tick_count": 117
            }
        }
    
    Example expected_trades:
        [
            {"signal_tick": 10, "direction": "LONG", "lot_size": 0.01},
            {"signal_tick": 310, "direction": "SHORT", "lot_size": 0.01}
        ]
    """
    warmup_errors: List[str] = field(default_factory=list)
    bar_snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    expected_trades: List[Dict[str, Any]] = field(default_factory=list)
    tick_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize to dict for JSON/pickle transfer.
        
        All fields are already JSON-serializable:
        - warmup_errors: List[str]
        - bar_snapshots: Dict[str, Dict] (bars already converted via Bar.to_dict())
        - expected_trades: List[Dict]
        - tick_count: int
        
        Returns:
            Dict ready for JSON serialization
        """
        return {
            'warmup_errors': self.warmup_errors,
            'bar_snapshots': self.bar_snapshots,
            'expected_trades': self.expected_trades,
            'tick_count': self.tick_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BacktestingMetadata':
        """
        Deserialize from dict.
        
        Used when reconstructing from JSON/pickle transfer.
        
        Args:
            data: Dict from to_dict()
            
        Returns:
            BacktestingMetadata instance
        """
        return cls(
            warmup_errors=data.get('warmup_errors', []),
            bar_snapshots=data.get('bar_snapshots', {}),
            expected_trades=data.get('expected_trades', []),
            tick_count=data.get('tick_count', 0)
        )
    
    def has_warmup_errors(self) -> bool:
        """Check if any warmup validation errors occurred."""
        return len(self.warmup_errors) > 0
    
    def get_snapshot_count(self) -> int:
        """Get number of captured bar snapshots."""
        return len(self.bar_snapshots)
    
    def get_expected_trade_count(self) -> int:
        """Get number of expected trades in sequence."""
        return len(self.expected_trades)
