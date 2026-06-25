"""
Splitter Factory
================
Resolves a GenerationStrategy to its Splitter implementation (name → class registry,
mirroring the WorkerFactory pattern). The split strategy is therefore config-driven: a new
strategy is added by registering one class here.

Lives in the generator domain (not framework/factory/) because it composes generator-domain
splitters — framework must not depend on python/scenario/ (layering).
"""

from typing import Dict, Type

from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.scenario.generator.splitters.abstract_splitter import AbstractSplitter
from python.scenario.generator.splitters.blocks_split import BlocksSplit
from python.scenario.generator.splitters.continuous_split import ContinuousSplit
from python.scenario.generator.splitters.volatility_split import VolatilitySplit
from python.scenario.generator.splitters.walk_forward_split import WalkForwardSplit


class SplitterFactory:
    """Creates splitter instances from a GenerationStrategy."""

    def __init__(self):
        """Initialize the factory with the strategy → splitter-class registry."""
        # Registry: strategy → splitter class
        self._registry: Dict[GenerationStrategy, Type[AbstractSplitter]] = {
            GenerationStrategy.BLOCKS: BlocksSplit,
            GenerationStrategy.VOLATILITY_SPLIT: VolatilitySplit,
            GenerationStrategy.CONTINUOUS: ContinuousSplit,
            GenerationStrategy.WALK_FORWARD: WalkForwardSplit,
        }

    def create_splitter(
        self,
        strategy: GenerationStrategy,
        config: object,
    ) -> AbstractSplitter:
        """
        Create a splitter for the given strategy.

        Args:
            strategy: Generation strategy to resolve
            config: Strategy-specific config (BlocksStrategyConfig for blocks,
                ProfileStrategyConfig for volatility_split / continuous / walk_forward)

        Returns:
            Splitter instance

        Raises:
            ValueError: If the strategy is not registered
        """
        splitter_class = self._registry.get(strategy)
        if splitter_class is None:
            raise ValueError(
                f"Unknown generation strategy: '{strategy}'. "
                f"Available: {[s.value for s in self._registry]}"
            )
        return splitter_class(config)
