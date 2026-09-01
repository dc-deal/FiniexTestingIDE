"""
FiniexTestingIDE - Connection Policy Configuration Types
The one schema every external connection's retry settings validate against (#473).

One schema, several homes. The numbers stay in the config file that owns their domain —
the producer's settings in sentiment_config.json, the tick source's in the AutoTrader
profile, the broker's in market_config.json — but they are the SAME fields with the same
names and the same defaults everywhere. What is decided in one place is the classification
and the vocabulary, not the values.

§28: every default here is mirrored with the identical value in its config file.
"""

from pydantic import BaseModel

from python.framework.types.connection_types import GiveUpAction


class ConnectionPolicy(BaseModel):
    """
    Retry ladder and give-up rule for one external connection.

    Args:
        initial_delay_s: Delay before the first retry; doubles from there
        max_delay_s: Cap the doubling never exceeds
        jitter: Spread the delay over [0.5, 1.0) of its value, so a fleet of clients
            does not reconnect in lockstep — a self-inflicted thundering herd
        attempt_budget: Attempts before giving up; 0 = never give up (a long-lived
            connection whose whole job is to come back)
        request_timeout_s: Socket timeout for one attempt
        on_give_up: What the caller does once the ladder stops trying
    """
    initial_delay_s: float = 1.0
    max_delay_s: float = 60.0
    jitter: bool = True
    attempt_budget: int = 0
    request_timeout_s: float = 15.0
    on_give_up: GiveUpAction = GiveUpAction.ALERT
