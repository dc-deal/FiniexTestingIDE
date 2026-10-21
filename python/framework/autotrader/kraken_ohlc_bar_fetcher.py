"""
FiniexTestingIDE - Kraken OHLC Bar Fetcher
Fetches historical OHLC bars from Kraken public REST API for warmup.

Public endpoint — no authentication required.
"""

from datetime import datetime, timezone
from typing import Dict, FrozenSet, List, Optional, Set

import requests

from python.framework.exceptions.timeframe_errors import UnsupportedTimeframeError
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.adapters.kraken_error_codes import raise_for_kraken_errors
from python.framework.types.market_types.market_data_types import Bar
from python.framework.utils.bar_period import is_complete_at
from python.framework.utils.http_attempt import send_classified
from python.framework.utils.timeframe_config_utils import TimeframeConfig

# Mirrors BrokerTransportConfig.request_timeout_s, which is where an operator tunes it.
# Present as a constant only for the direct-construction paths (tests, CLI probes) that
# have no broker entry in scope.
_DEFAULT_REQUEST_TIMEOUT_S: float = 15.0


class KrakenOhlcBarFetcher:
    """
    Fetches OHLC bars from Kraken public API.

    Uses GET /0/public/OHLC — no authentication needed.
    Converts API response directly to Bar objects.

    Args:
        logger: ScenarioLogger for status messages
    """

    API_BASE = 'https://api.kraken.com'

    # The ONE venue fact: which OHLC interval lengths this endpoint publishes, in minutes.
    #
    # Deliberately NOT a timeframe table. A second name->minutes map beside
    # TimeframeConfig._REGISTRY would be the same rule written twice, and the two would
    # disagree the first time either moved — which is exactly what happened when M10 was
    # added to the vocabulary. Names and durations belong to the registry; what belongs
    # here is only which of those durations the venue will serve.
    PUBLISHED_INTERVAL_MINUTES: FrozenSet[int] = frozenset(
        {1, 5, 15, 30, 60, 240, 1440, 10080, 21600}
    )

    # Standard symbol → Kraken pair name for OHLC endpoint
    SYMBOL_TO_KRAKEN_PAIR: Dict[str, str] = {
        'BTCUSD': 'XBTUSD',
        'BTCEUR': 'XBTEUR',
        'ETHUSD': 'ETHUSD',
        'ETHEUR': 'ETHEUR',
        'SOLUSD': 'SOLUSD',
        'ADAUSD': 'ADAUSD',
        'XRPUSD': 'XRPUSD',
        'LTCUSD': 'LTCUSD',
        'DASHUSD': 'DASHUSD',
    }

    def __init__(
        self,
        logger: Optional[ScenarioLogger] = None,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
    ):
        self._logger = logger
        self._request_timeout_s = request_timeout_s

    @classmethod
    def supported_warmup_timeframes(cls) -> Set[str]:
        """
        Timeframes this venue can serve as warmup bars.

        Derived from the project's timeframe vocabulary rather than listed again here, so
        a timeframe added to the registry is answered correctly without anyone remembering
        to update a second table.

        Returns:
            The registry timeframes whose duration the venue publishes
        """
        return {
            timeframe for timeframe in TimeframeConfig.sorted()
            if TimeframeConfig.get_minutes(timeframe) in cls.PUBLISHED_INTERVAL_MINUTES
        }

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        count: int,
        reference_time: datetime,
    ) -> List[Bar]:
        """
        Fetch the newest `count` bars that are complete at an instant.

        A row is taken only when BOTH sides call it finished: its period has ended by the
        reference instant (bar_period — the same rule the archive selects by), and the venue
        has committed it — Kraken answers with a `last` field naming the newest committed
        period, and the row after it is the period still forming. Measured on M1 ETHUSD: a
        period was committed about 0.8 s after it ended on 2026-10-12, between 1.5 and 2.2 s on
        2026-10-13, and a fresh row for the next period appears at the same moment
        (python/experiments/venue_probes/probe_kraken_ohlc_commit.py repeats it). Either test
        alone fails near a boundary: our
        clock may already be past the period's end while the venue still adds trades to it,
        and dropping the last row by POSITION — as this method once did — keeps or drops a
        row by where it stands rather than by what it is.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')
            timeframe: Timeframe string (e.g., 'M5', 'H1')
            count: Number of completed bars to return
            reference_time: The instant the bars must be complete at

        Returns:
            List of Bar objects (oldest first)
        """
        # One lookup for both uses: the venue's interval code IS the timeframe's duration
        # in minutes, so there is nothing to translate and nothing to keep in step.
        interval_minutes = TimeframeConfig.get_minutes(timeframe)
        if interval_minutes not in self.PUBLISHED_INTERVAL_MINUTES:
            raise UnsupportedTimeframeError(
                timeframe,
                f"Kraken's OHLC endpoint does not publish a {interval_minutes}-minute "
                f"interval, so '{timeframe}' cannot be warmed up from it. "
                f'Supported: {sorted(self.supported_warmup_timeframes())}. '
                f'A timeframe may exist in the project registry and be renderable from '
                f'the archive without the venue offering it live.',
            )
        interval = interval_minutes

        pair = self.SYMBOL_TO_KRAKEN_PAIR.get(symbol, symbol)

        # Request slightly more bars than needed to handle gaps
        since_ts = int(
            reference_time.timestamp()
            - (count + 10) * interval_minutes * 60
        )

        # === API call ===
        data = self._fetch_ohlc(pair, interval, since_ts)

        # Find the result key (Kraken uses pair name as key)
        bars_data = self._extract_bars_data(data, pair)
        if bars_data is None:
            self._log_warning(
                f'No OHLC data returned for {pair} {timeframe}'
            )
            return []

        committed_until = data.get('last')
        bars = [
            self._row_to_bar(row, symbol, timeframe)
            for row in bars_data
            if (committed_until is None or int(row[0]) <= int(committed_until))
            and is_complete_at(
                datetime.fromtimestamp(int(row[0]), tz=timezone.utc), timeframe, reference_time)
        ]

        # Take only the last `count` bars
        if len(bars) > count:
            bars = bars[-count:]

        return bars

    # =========================================================================
    # HTTP
    # =========================================================================

    def _fetch_ohlc(
        self,
        pair: str,
        interval: int,
        since: int,
    ) -> Dict:
        """
        GET /0/public/OHLC with pair, interval, since parameters.

        Args:
            pair: Kraken pair name (e.g., 'XBTUSD')
            interval: Interval in minutes (e.g., 5)
            since: Unix timestamp for since parameter

        Returns:
            API result dict
        """
        url = f'{self.API_BASE}/0/public/OHLC'
        params = {
            'pair': pair,
            'interval': interval,
            'since': since,
        }

        # Classified, so the warmup's ladder retries a timeout instead of giving up at the first (#598)
        response = send_classified(
            lambda: requests.get(url, params=params, timeout=self._request_timeout_s),
            '/0/public/OHLC')

        data = response.json()
        # The venue's own "not now" goes to the ladder as transient, so the warmup retries it
        # (#598); any other error is its answer
        raise_for_kraken_errors(data.get('error', []), 'Kraken OHLC API error')

        return data.get('result', {})

    # =========================================================================
    # CONVERSION
    # =========================================================================

    @staticmethod
    def _row_to_bar(row: list, symbol: str, timeframe: str) -> Bar:
        """
        Convert a Kraken OHLC row to a Bar object.

        Kraken OHLC row format: [time, open, high, low, close, vwap, volume, count]

        Args:
            row: Single OHLC row from API response
            symbol: Standard symbol
            timeframe: Timeframe string

        Returns:
            Bar object
        """
        return Bar(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.fromtimestamp(row[0], tz=timezone.utc).isoformat(),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[6]),
            tick_count=int(row[7]),
        )

    def _extract_bars_data(self, data: Dict, pair: str) -> Optional[list]:
        """
        Extract bar data from API response.

        Kraken returns the pair data under the pair name key, but the
        exact key can vary (e.g., 'XXBTZUSD' vs 'XBTUSD').

        Args:
            data: API result dict
            pair: Requested pair name

        Returns:
            List of OHLC rows, or None if not found
        """
        # Direct match first
        if pair in data:
            return data[pair]

        # Try keys that aren't 'last' (Kraken includes 'last' as metadata)
        for key, value in data.items():
            if key != 'last' and isinstance(value, list):
                return value

        return None

    # =========================================================================
    # LOGGING
    # =========================================================================

    def _log_warning(self, message: str) -> None:
        """Log warning if logger available."""
        if self._logger:
            self._logger.warning(message)
