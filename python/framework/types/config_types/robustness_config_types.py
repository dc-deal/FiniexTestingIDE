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
    EXPECTANCY = 'expectancy'   # mean R-multiple — currency/instrument-neutral, needs a stop on the position
    NET_PNL = 'net_pnl'         # account-currency P&L — always measured, not comparable across currencies (default)
    # Final equity against the window's starting value, in percent, with everything still open
    # marked at the last price. The measure for a strategy that is invested all the time: its
    # result sits in what it holds, which the two closed-trade metrics above never see.
    EQUITY_RETURN_PCT = 'equity_return_pct'

    def values_open_positions(self) -> bool:
        """
        Whether this metric values what a window still holds when it ends.

        Such a metric is not distorted by positions left open at a block edge, so the
        block-splitting disposition is no trust input for it.

        Returns:
            True for a mark-to-market metric, False for a closed-trade metric
        """
        return self is RobustnessMetric.EQUITY_RETURN_PCT


class RobustnessConfig(BaseModel):
    """
    Set-wide robustness validation settings (the top-level `robustness` block).

    Disabled by default — the report + verdict only activate when `enabled` is true.
    """
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    metric: RobustnessMetric = RobustnessMetric.NET_PNL
    # Time-ordered IS/OOS split used by the generator (last fraction → out_of_sample).
    oos_split: float = 0.3
    # Distribution below this window count is statistically weak → advisory.
    min_windows: int = 3
    # Walk-Forward Efficiency (OOS metric / IS metric) verdict thresholds.
    overfit_wfe_threshold: float = 0.5   # WFE below → OVERFIT advisory
    robust_wfe_threshold: float = 0.8    # WFE at/above → ROBUST
    # Block-splitting disposition above which the per-window numbers are artifacts → the
    # verdict is suppressed. This is the ONE trust decision about block-splitting distortion;
    # the report section shows a magnitude scale (GOOD/MODERATE/HIGH/SEVERE) that deliberately
    # makes no trust claim, so the two cannot disagree when this value is tuned.
    disposition_trust_pct: float = 25.0
