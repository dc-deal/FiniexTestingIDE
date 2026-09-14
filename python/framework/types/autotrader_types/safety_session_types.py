"""
FiniexTestingIDE - Safety Session Types

What the circuit breaker SAW over one live session, captured raw for the report (#356/#314).

The breaker's own state answers one question — is the bot blocked right now — and that is
the wrong question at the end of a thirty-day run. A session-end snapshot showing
`blocked=false, drawdown 2 %` describes a session that touched 18 % at hour three and
recovered exactly as it describes one that never moved. So the loop carries a running
maximum beside the live state, and the report reads that.

The daily figures are per DAY rather than aggregated, for the same reason one step down: a
maximum over thirty days would be a maximum over thirty different denominators, which is a
number about nothing. One row per day, each naming its own baseline.

Captured here, derived in the builder, rendered nowhere else (#391).
"""

from dataclasses import dataclass, field
from typing import List, Optional

from python.framework.types.persistence_types import RiskBaseline


@dataclass
class SafetyDayRecord:
    """
    One UTC trading day, its own denominator, and the worst loss measured against it.

    Args:
        day: The UTC date, YYYY-MM-DD
        baseline: The DAY_START record this day's loss was measured against
        worst_loss_abs: The largest drop below that baseline seen during the day
        worst_loss_pct: The same drop as a percentage of it. ONE instant, not two: a
            DAY_START baseline does not move inside its own day, so the deepest absolute
            loss and the deepest percentage loss are necessarily the same moment
        worst_loss_at: When that low was seen, ISO-8601 UTC (canonical clock)
        limit_hit: Whether a DAILY limit fired on this day. Distinct from the session
            block: a day can trip its own limit while the session drawdown stays inside
            its threshold, which is the whole reason the daily limit exists
    """
    day: str
    baseline: Optional[RiskBaseline] = None
    worst_loss_abs: float = 0.0
    worst_loss_pct: float = 0.0
    worst_loss_at: str = ''
    limit_hit: bool = False


@dataclass
class SafetySessionRecord:
    """
    The session's risk denominator, how far the account moved against it, and what fired.

    Recorded whether the breaker is enabled or not. A session running with the limits OFF
    still measures its drawdown against the baseline, and for a parity proof that is the
    interesting record — it says what WOULD have fired before anything is armed.

    Args:
        enabled: Whether the circuit breaker was switched on for this session
        spot_mode: Whether the account holds inventory. Stamped at CAPTURE from the
            session's resolved trading model, because that is where it is authoritatively
            known — a report stage re-resolving it would be a second answer to a settled
            question, and the two can only drift apart
        baseline: The session denominator, as the record that describes itself. None when
            no baseline was ever taken (a session that saw no valuable tick)
        final_value: The last account value the breaker checked
        worst_drawdown_abs: The largest drop below the baseline seen in the session
        worst_drawdown_abs_at: When that low was seen, ISO-8601 UTC (canonical clock)
        worst_drawdown_pct: The deepest drop as a PERCENTAGE of the baseline — tracked
            separately because a HIGH_WATER_MARK baseline moves, and then the two extremes
            are two different moments. 1 000 against a baseline of 10 000 is 10 %; 1 200
            against a later peak of 20 000 is the larger amount and the smaller share, so
            one figure alone always understates one of the two limits. With the default
            fixed baseline they are the same instant and agree by construction
        worst_drawdown_pct_at: When THAT low was seen, ISO-8601 UTC
        block_count: How often the soft block ENGAGED — transitions into blocked, not ticks
            spent blocked. One number the operator can read as "how often did this happen"
        blocked_at_end: Whether new entries were still blocked when the session ended
        reason_at_end: The breaker's reason at that moment, empty when it was not blocked
        days: One row per UTC day the session ran through
        flatten_fired: Whether the HARD stop tripped
        flatten_reason: What tripped it, with its numbers
        flatten_completed: Whether the book was confirmed flat before the session ended.
            None when the hard stop never fired — which is not the same as False, and a
            reader must be able to tell "did not fire" from "fired and did not finish"
        flatten_unconfirmed: The positions still open when the session ended anyway
    """
    enabled: bool = False
    spot_mode: bool = False
    baseline: Optional[RiskBaseline] = None
    final_value: float = 0.0
    worst_drawdown_abs: float = 0.0
    worst_drawdown_abs_at: str = ''
    worst_drawdown_pct: float = 0.0
    worst_drawdown_pct_at: str = ''
    block_count: int = 0
    blocked_at_end: bool = False
    reason_at_end: str = ''
    days: List[SafetyDayRecord] = field(default_factory=list)
    flatten_fired: bool = False
    flatten_reason: str = ''
    flatten_completed: Optional[bool] = None
    flatten_unconfirmed: List[str] = field(default_factory=list)
