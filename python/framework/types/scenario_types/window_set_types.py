"""
Window Set Types
================
Unified window model for the scenario generator.

A `GeneratedWindow` is one labeled time range for a single symbol; a `WindowSet` is the
producer-agnostic collection a `Splitter` emits. This replaces the former divergent pair
`ScenarioCandidate` (blocks path) and `ProfileBlock` / `GeneratorProfile` (profile path).

Pure data model — serialization lives in the present-layer (`WindowSetSerializer`), never on
these types (model-first; same separation as the reporting pipeline).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy


@dataclass
class WindowSplitConfig:
    """
    How a WindowSet was split — recorded for the profile artifact (provenance).

    Carries the volatility-split parameters (populated for volatility_split / continuous
    modes; None for blocks, which serialize to a scenario-set JSON without this metadata).
    """
    min_block_hours: int
    max_block_hours: int
    atr_percentile_threshold: int
    split_algorithm: str


@dataclass
class GeneratedWindow:
    """
    One labeled time window for a symbol.

    Pure window description — carries NO role (assigned by the WindowMaterializer) and NO
    strategy parameters (the generator output is parameter-agnostic by design: a WindowSet
    is produced once and reused by every parameter combination of a sweep, #32).
    """
    block_index: int
    start_time: datetime
    end_time: datetime
    regime: VolatilityRegime
    session: TradingSession
    estimated_ticks: int
    atr: float
    split_reason: str = ''
    tick_density: float = 0.0
    distance_to_next_block_hours: Optional[float] = None

    @property
    def block_duration_hours(self) -> float:
        """
        Window duration in hours (derived from start/end).

        Returns:
            Duration in hours
        """
        return (self.end_time - self.start_time).total_seconds() / 3600


@dataclass
class WindowSet:
    """
    A producer-agnostic set of generated windows for one symbol.

    Unifies the former `GenerationResult` (blocks) and `GeneratorProfile` (profile) — the
    single output type every `Splitter` returns and the materializer consumes.
    """
    symbol: str
    broker_type: str
    strategy: GenerationStrategy
    windows: List[GeneratedWindow]
    generated_at: datetime
    # generator_mode string ('blocks' | 'volatility_split' | 'continuous') — the report's
    # symbol → mode lookup; mirrors the legacy ProfileMetadata.generator_mode.
    mode: str = ''
    split_config: Optional[WindowSplitConfig] = None
    discovery_fingerprints: dict = field(default_factory=dict)

    @property
    def block_count(self) -> int:
        """
        Number of windows in the set.

        Returns:
            Window count
        """
        return len(self.windows)

    @property
    def total_coverage_hours(self) -> float:
        """
        Total time covered by all windows in hours.

        Returns:
            Sum of window durations in hours
        """
        return sum(w.block_duration_hours for w in self.windows)
