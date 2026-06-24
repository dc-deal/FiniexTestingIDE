"""
Robustness role assignment (#367) — the single time-ordered IS/OOS split policy.

Shared by BOTH generator producer paths (the blocks-JSON saver and the in-memory
`load_from_profiles`), so the role policy lives in one place. Time-ordered: the first
(1 - oos_split) fraction of the chronological windows is In-Sample, the trailing fraction is
Out-of-Sample — never train on the future (the institutional default).
"""
from typing import List

from python.framework.types.config_types.robustness_config_types import RobustnessRole


def assign_roles_time_ordered(count: int, oos_split: float) -> List[RobustnessRole]:
    """
    Assign IS/OOS roles to chronologically-ordered windows by a trailing split.

    Args:
        count: Number of windows (assumed in chronological order)
        oos_split: Fraction of trailing windows assigned to Out-of-Sample (0..1)

    Returns:
        One RobustnessRole per window; for count >= 2 always at least one IS and one OOS
    """
    if count <= 0:
        return []
    if count == 1:
        # A single window cannot be split — it is the In-Sample set.
        return [RobustnessRole.IN_SAMPLE]

    n_oos = round(count * oos_split)
    n_oos = max(1, min(n_oos, count - 1))  # guarantee at least one IS and one OOS
    n_is = count - n_oos
    return [RobustnessRole.IN_SAMPLE] * n_is + [RobustnessRole.OUT_OF_SAMPLE] * n_oos
