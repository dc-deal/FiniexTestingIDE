"""
FiniexTestingIDE - Kraken OHLC Bar Fetcher
Fetches historical OHLC bars from Kraken public REST API for warmup.

Public endpoint — no authentication required.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.market_types.market_data_types import Bar
from python.framework.utils.timeframe_config_utils import TimeframeConfig


class KrakenOhlcBarFetcher:
    """
    Fetches OHLC bars from Kraken public API.

    Uses GET /0/public/OHLC — no authentication needed.
    Converts API response directly to Bar objects.

    Args:
        logger: ScenarioLogger for status messages
    """

    API_BASE = 'https://api.kraken.com'
    REQUEST_TIMEOUT_S = 15

    # Kraken OHLC interval codes (minutes)
    TIMEFRAME_TO_INTERVAL: Dict[str, int] = {
        'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30,
        'H1': 60, 'H4': 240, 'D1': 1440,
    }

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

    def __init__(self, logger: Optional[ScenarioLogger] = None):
        self._logger = logger

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        count: int,
    ) -> List[Bar]:
        """
        Fetch historical OHLC bars from Kraken API.

        Drops the last bar (Kraken returns the current in-progress bar).
        Returns only the last `count` completed bars.

        Args:
            symbol: Standard symbol (e.g., 'BTCUSD')
            timeframe: Timeframe string (e.g., 'M5', 'H1')
            count: Number of completed bars to return

        Returns:
            List of Bar objects (oldest first)
        """
        interval = self.TIMEFRAME_TO_INTERVAL.get(timeframe)
        if interval is None:
            raise ValueError(
                f"Unsupported timeframe '{timeframe}' for Kraken OHLC. "
                f"Supported: {list(self.TIMEFRAME_TO_INTERVAL.keys())}"
            )

        pair = self.SYMBOL_TO_KRAKEN_PAIR.get(symbol, symbol)

        # Request slightly more bars than needed to handle gaps
        interval_minutes = TimeframeConfig.get_minutes(timeframe)
        since_ts = int(
            (datetime.now(timezone.utc).timestamp())
            - (count + 10) * interval_minutes * 60
        )

        # === API call ===
        data = self._fetch_ohlc(pair, interval, since_ts)

        # Find the result key (Kraken uses pair name as key)
        bars_data = self._extract_bars_data(data, pair)
        if bars_data is None:
            self._log_warning(
                f"No OHLC data returned for {pair} {timeframe}"
            )
            return []

        # Convert to Bar objects — drop last bar (in-progress)
        if len(bars_data) > 1:
            bars_data = bars_data[:-1]

        bars = [
            self._row_to_bar(row, symbol, timeframe)
            for row in bars_data
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
        url = f"{self.API_BASE}/0/public/OHLC"
        params = {
            'pair': pair,
            'interval': interval,
            'since': since,
        }

        response = requests.get(url, params=params, timeout=self.REQUEST_TIMEOUT_S)
        response.raise_for_status()

        data = response.json()
        errors = data.get('error', [])
        if errors:
            raise ConnectionError(f"Kraken OHLC API error: {errors}")

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
            is_complete=True,
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
