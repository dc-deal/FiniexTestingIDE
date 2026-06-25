"""
Walk-Forward Split Strategy
===========================
Registered extension point for walk-forward / rolling-fold splitting — NOT YET IMPLEMENTED.

Walk-forward optimization is the cross product of the data axis (this splitter's folds) with
the parameter axis (the #32 sweep): each parameter combination is evaluated over rolling
(train → test) window pairs, and in-sample vs out-of-sample degradation is measured per
combination. The structural slot exists here so the strategy is config-discoverable and the
materializer/report seam is ready; the algorithm lands with #32 Phase 4 (overlaps #367 IS/OOS,
which already provides the single-fold role labeling and the degradation/WFE math).
"""

from datetime import datetime
from typing import Optional

from python.framework.types.scenario_types.scenario_generator_types import ProfileStrategyConfig
from python.framework.types.scenario_types.window_set_types import WindowSet
from python.scenario.generator.splitters.abstract_splitter import AbstractSplitter


class WalkForwardSplit(AbstractSplitter):
    """Rolling (train → test) fold splitter — structural stub for #32 Phase 4."""

    def __init__(self, config: ProfileStrategyConfig):
        """
        Initialize walk-forward splitter.

        Args:
            config: Profile strategy configuration (fold sizing reuses these bounds)
        """
        self._config = config

    def split(
        self,
        broker_type: str,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        count_max: Optional[int] = None,
    ) -> WindowSet:
        """
        Produce rolling walk-forward folds — not yet implemented.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start_time: Profile start time (UTC)
            end_time: Profile end time (UTC)
            count_max: Optional fold cap

        Returns:
            WindowSet with fold-grouped windows (once implemented)
        """
        raise NotImplementedError(
            'WalkForwardSplit is a structural extension point — the rolling-fold algorithm '
            'lands with #32 Phase 4 (walk-forward optimization), building on the #367 IS/OOS '
            'role + degradation/WFE math. Use blocks / volatility_split / continuous for now.'
        )
