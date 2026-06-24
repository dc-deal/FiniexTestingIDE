"""
FiniexTestingIDE - Robustness Validation Configuration Types
Pydantic models + enums for the scenario-set `robustness` block (#367).

The robustness block is a top-level, set-wide mode (sibling of `scenario_set_name`), NOT a
per-scenario cascade default. `role` is the per-scenario window label. Defaults live here +
in the robustness user guide (a per-set block has no app_config mirror).
"""
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RobustnessRole(StrEnum):
    """Per-scenario window label for In-Sample / Out-of-Sample validation."""
    IN_SAMPLE = 'in_sample'
    OUT_OF_SAMPLE = 'out_of_sample'
    UNASSIGNED = 'unassigned'


class RobustnessMetric(StrEnum):
    """The per-window metric the robustness report distributes + compares IS vs OOS over."""
    EXPECTANCY = 'expectancy'   # mean R-multiple — currency/instrument-neutral (default)
    NET_PNL = 'net_pnl'         # account-currency P&L — not comparable across currencies


class RobustnessConfig(BaseModel):
    """
    Set-wide robustness validation settings (the top-level `robustness` block).

    Disabled by default — the report + verdict only activate when `enabled` is true.
    """
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    metric: RobustnessMetric = RobustnessMetric.EXPECTANCY
    # Time-ordered IS/OOS split used by the generator (last fraction → out_of_sample).
    oos_split: float = 0.3
    # Distribution below this window count is statistically weak → advisory.
    min_windows: int = 3
    # Walk-Forward Efficiency (OOS metric / IS metric) verdict thresholds.
    overfit_wfe_threshold: float = 0.5   # WFE below → OVERFIT advisory
    robust_wfe_threshold: float = 0.8    # WFE at/above → ROBUST
    # Block-splitting disposition above which the per-window numbers are artifacts → the
    # verdict is suppressed (mirrors the block-splitting UNRELIABLE class, > 25%).
    disposition_trust_pct: float = 25.0
