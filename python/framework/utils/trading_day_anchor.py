"""
FiniexTestingIDE - Trading Day Anchor (#476)

Where a market's trading day flips, and which trading day an instant belongs to.

Three places used to answer this independently, all of them from the tick stamp and all of
them at midnight UTC: the session-log rotation, the daily-loss baseline (#314), and — once
it exists — the record seal. Midnight UTC is right for crypto by coincidence and wrong for
forex, whose day flips at the swap rollover (17:00 America/New_York). Two further
consequences of deriving it from the TICK: a silent feed over the boundary misses it
entirely, and a replay and a live session can disagree.

Pure functions over an anchor and an instant — no tick, no config manager, no state. The
anchor is resolved once by `MarketConfigManager.get_trading_day_anchor`; the caller passes
the instant from the CANONICAL clock, which the heartbeat advances while the market is quiet.

The trading day is labelled by the calendar date, in the anchor's own timezone, on which it
OPENED. So the forex session that begins Monday 17:00 New York is Monday's, not Tuesday's.
It is an internal segment label rather than a venue statement; what it has to be is the same
answer everywhere, which is the whole reason this module exists.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from python.framework.types.config_types.market_config_types import DayAnchorConfig
from python.framework.utils.time_utils import local_time_to_utc


def boundary_opening(day: date, anchor: DayAnchorConfig) -> datetime:
    """
    The UTC instant at which the trading day labelled `day` opened.

    DST-aware through `local_time_to_utc`, so 17:00 New York is 21:00 UTC in summer and
    22:00 in winter. A local time that does not exist on a spring-forward date resolves to
    the shifted instant rather than raising; neither anchor this project configures falls
    in such a gap.

    Args:
        day: The trading day's label, a calendar date in the anchor's timezone
        anchor: Where this market's day flips

    Returns:
        Timezone-aware UTC instant of that day's opening boundary
    """
    return local_time_to_utc(day, anchor.local_time, anchor.timezone)


def trading_day_of(instant: datetime, anchor: DayAnchorConfig) -> date:
    """
    Which trading day an instant falls in.

    The instant is placed on the anchor's local calendar first, then moved back one day if
    it sits before that date's boundary — which is what separates 16:00 New York (still the
    previous session) from 18:00 (the new one) on the same calendar date.

    Args:
        instant: A timezone-aware instant, normally from the canonical clock
        anchor: Where this market's day flips

    Returns:
        The trading day's label as a calendar date in the anchor's timezone
    """
    local_day = instant.astimezone(ZoneInfo(anchor.timezone)).date()

    if instant < boundary_opening(local_day, anchor):
        return local_day - timedelta(days=1)
    return local_day
