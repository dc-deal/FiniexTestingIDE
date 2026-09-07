"""
FiniexTestingIDE - Live Execution Errors
Exception types for live trading failures (Horizon 2).

Used by LiveTradeExecutor and broker adapters to signal
broker communication and order execution failures.
"""

from python.framework.exceptions.finiex_error import FiniexError


class DryRunConflictError(FiniexError):
    """
    A profile tried to enable real orders that the broker's global setting forbids.

    A profile may TIGHTEN the dry-run posture (`true` when the broker default is live)
    and never loosen it. Profiles are copied, shared and edited quickly; enabling real
    money is a deliberate act on the broker's own configuration, not a side effect of
    picking up someone's profile file.
    """
    pass


class SessionEndPolicyConflictError(FiniexError):
    """
    A profile asked for `session_end.orders: 'leave'` where nothing would manage them.

    'leave' is the LOOSENING value — afterwards orders sit at a venue with nobody
    watching — and it is refused in three distinct situations, not one:

    - The broker's own setting forbids it. A profile may TIGHTEN the posture ('cancel'
      against a 'leave' broker default) and never loosen it, exactly as with dry_run:
      profiles are copied, shared and edited quickly, so leaving live orders behind is a
      deliberate act on the broker's own configuration.
    - Cold start is off, so no later session would ever adopt the orders back.
    - Cold start would adopt them but its mode is 'operator_confirm' on an unattended
      start, so the next boot could not ask anyone and would refuse — the bot meant to
      manage the orders would not start.

    All three say the same thing: an order may only be left standing when something is
    going to pick it up again.
    """
    pass


class SessionEndCloseUnsupportedError(FiniexError):
    """
    A profile asked the session end to CLOSE open positions at the venue (#492).

    The value is declared so the policy can express it, and refused because it is not
    built: a real close is an asynchronous live order whose fill arrives on the next tick,
    and at session end the tick source is already stopped and the request worker is about
    to die. It needs the unresolved-write resolution (#487) first. Refusing is the point —
    silently behaving like 'leave' would leave a profile claiming something it does not do.
    """
    pass
