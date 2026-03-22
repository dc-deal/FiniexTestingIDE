"""
Generator Profile Types
========================
Type definitions for the Generator Profile System.

A Generator Profile is a pre-computed, immutable JSON artifact containing
block definitions with metadata. It separates the compute-heavy generation
phase from tick-run execution.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)


@dataclass
class ProfileSplitConfig:
    """Configuration used for profile generation."""
    min_block_hours: int
    max_block_hours: int
    atr_percentile_threshold: int
    split_algorithm: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            'min_block_hours': self.min_block_hours,
            'max_block_hours': self.max_block_hours,
            'atr_percentile_threshold': self.atr_percentile_threshold,
            'split_algorithm': self.split_algorithm,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProfileSplitConfig':
        """
        Create from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            ProfileSplitConfig instance
        """
        return cls(
            min_block_hours=data['min_block_hours'],
            max_block_hours=data['max_block_hours'],
            atr_percentile_threshold=data['atr_percentile_threshold'],
            split_algorithm=data['split_algorithm'],
        )


@dataclass
class ProfileBlock:
    """A single block within a generator profile."""
    block_index: int
    start_time: datetime
    end_time: datetime
    block_duration_hours: float
    split_reason: str
    atr_at_split: float
    regime_at_split: VolatilityRegime
    session: TradingSession
    estimated_ticks: int
    distance_to_next_block_hours: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        result = {
            'block_index': self.block_index,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'block_duration_hours': self.block_duration_hours,
            'split_reason': self.split_reason,
            'atr_at_split': self.atr_at_split,
            'regime_at_split': self.regime_at_split.value,
            'session': self.session.value,
            'estimated_ticks': self.estimated_ticks,
        }
        if self.distance_to_next_block_hours is not None:
            result['distance_to_next_block_hours'] = self.distance_to_next_block_hours
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProfileBlock':
        """
        Create from dictionary.

        Args:
            data: Block dictionary

        Returns:
            ProfileBlock instance
        """
        return cls(
            block_index=data['block_index'],
            start_time=datetime.fromisoformat(data['start_time']),
            end_time=datetime.fromisoformat(data['end_time']),
            block_duration_hours=data['block_duration_hours'],
            split_reason=data['split_reason'],
            atr_at_split=data['atr_at_split'],
            regime_at_split=VolatilityRegime(data['regime_at_split']),
            session=TradingSession(data['session']),
            estimated_ticks=data.get('estimated_ticks', 0),
            distance_to_next_block_hours=data.get('distance_to_next_block_hours'),
        )


@dataclass
class ProfileMetadata:
    """Metadata for a generator profile."""
    symbol: str
    broker_type: str
    generator_mode: str
    generated_at: datetime
    total_coverage_hours: float
    block_count: int
    discovery_fingerprints: Dict[str, str]
    split_config: ProfileSplitConfig

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            'symbol': self.symbol,
            'broker_type': self.broker_type,
            'generator_mode': self.generator_mode,
            'generated_at': self.generated_at.isoformat(),
            'total_coverage_hours': self.total_coverage_hours,
            'block_count': self.block_count,
            'discovery_fingerprints': self.discovery_fingerprints,
            'split_config': self.split_config.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ProfileMetadata':
        """
        Create from dictionary.

        Args:
            data: Metadata dictionary

        Returns:
            ProfileMetadata instance
        """
        return cls(
            symbol=data['symbol'],
            broker_type=data['broker_type'],
            generator_mode=data['generator_mode'],
            generated_at=datetime.fromisoformat(data['generated_at']),
            total_coverage_hours=data['total_coverage_hours'],
            block_count=data['block_count'],
            discovery_fingerprints=data.get('discovery_fingerprints', {}),
            split_config=ProfileSplitConfig.from_dict(data['split_config']),
        )


@dataclass
class GeneratorProfile:
    """
    Complete generator profile artifact.

    Immutable after generation. Contains all block definitions
    with metadata for reproducible batch execution.
    """
    profile_meta: ProfileMetadata
    blocks: List[ProfileBlock] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            'profile_meta': self.profile_meta.to_dict(),
            'blocks': [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GeneratorProfile':
        """
        Create from dictionary (loaded from JSON).

        Args:
            data: Profile dictionary

        Returns:
            GeneratorProfile instance
        """
        return cls(
            profile_meta=ProfileMetadata.from_dict(data['profile_meta']),
            blocks=[ProfileBlock.from_dict(b) for b in data.get('blocks', [])],
        )
