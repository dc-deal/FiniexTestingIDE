"""
FiniexTestingIDE - Submission Metadata Types
Snapshot of algo / market state at the moment an order was submitted.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SubmissionMetadata:
    """
    Snapshot of algo / market state at the moment an order was submitted.

    Currently carries the trade-channel mid price used by the SLIPPAGE audit
    channel (#340). Designed to absorb future submission-time fields without
    propagating parameter pairs through the entire pipeline (#345):
        - algo_decision_id (future)
        - decision_logic_version_hash (future, reproducibility)
        - intended_max_slippage_bps (future, OrderGuard pre-trade caps)

    Args:
        tick_mid_price: Trade-channel mid price at the submission moment.
            For crypto trade-channel feeds bid==ask==last, so mid collapses
            to the last trade price. None for synthetic cleanup pendings
            (scenario-end force-close has no algo submission moment).
        tick_time_msc: Tick time_msc at submission, for latency correlation
            in post-hoc analysis (#332 Field Study JSONL).
    """
    tick_mid_price: Optional[float] = None
    tick_time_msc: Optional[int] = None
