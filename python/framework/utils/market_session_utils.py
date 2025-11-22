"""
Berechnet Trading-Sessions neu basierend auf UTC-Zeit.

KORREKTE Forex Sessions (UTC):
- sydney_tokyo: 22:00 - 08:00 UTC (10h Asian session)
- london:       08:00 - 16:00 UTC (8h European session)  
- new_york:     13:00 - 21:00 UTC (8h US session)
- transition:   21:00 - 22:00 UTC (1h gap)

Note: London/NY overlap = 13:00-16:00 UTC (markiert als "london")
"""


from python.framework.types.scenario_generator_types import TradingSession


def get_session_from_utc_hour(hour):
    """Determine trading session from UTC hour"""
    if 22 <= hour <= 23 or 0 <= hour < 8:
        return TradingSession.SYDNEY_TOKYO
    elif 8 <= hour < 13:
        return TradingSession.LONDON
    elif 13 <= hour < 16:
        return TradingSession.LONDON  # London/NY overlap - bleibt "london"
    elif 16 <= hour < 21:
        return TradingSession.NEW_YORK
    else:  # 21:00 - 21:59
        return TradingSession.TRANSITION
