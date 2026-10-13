"""
Synthetic data for the warmup history suite.

ONE tick stream feeds every path, which is what makes their histories comparable at all:
  - the ARCHIVE is rendered from it by the importer's own renderer (VectorizedBarRenderer) and
    read back through the batch's own selection (SharedDataPreparator.prepare_bars);
  - the VENUE answers from it the way Kraken's OHLC endpoint answers at a given instant —
    one open-stamped row per period up to that instant, the last row the period still forming,
    and a `last` field naming the newest period the venue has committed (measured 2026-10-12).
Only the venue's transport is replaced; everything that parses, selects and injects is real.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from python.data_management.importers.vectorized_bar_renderer import VectorizedBarRenderer
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.process_data_types import BarRequirement, ProcessDataPackage
from python.framework.utils.bar_period import period_start
from python.framework.utils.timeframe_config_utils import TimeframeConfig

SYMBOL = 'ETHUSD'
BROKER_TYPE = 'kraken_spot'
SCENARIO = 'warmup_history_scenario'
UTC = timezone.utc


def tick_frame(start: datetime, end: datetime, step_s: int) -> pd.DataFrame:
    """
    A deterministic tick stream: one trade every `step_s` seconds, a price that wanders and turns.

    Args:
        start: First tick
        end: End of the stream (exclusive)
        step_s: Seconds between ticks

    Returns:
        Tick frame with the columns the importer's renderer reads
    """
    times = pd.date_range(start, end, freq=f'{step_s}s', inclusive='left', tz='UTC')
    index = np.arange(len(times))
    price = 1900.0 + 25.0 * np.sin(index / 37.0) + 0.013 * index + (index % 11) * 0.37
    return pd.DataFrame({
        'timestamp': times,
        'time_msc': times.asi8 // 1_000_000,
        'bid': price,
        'ask': price,
        'last': price,
        'price': price,
        'volume': 0.5 + (index % 7) * 0.1,
    })


def ticks_of(frame: pd.DataFrame, since: datetime) -> List[TickData]:
    """
    The stream from an instant on, as the tick loops receive it.

    Args:
        frame: The tick frame
        since: First instant included

    Returns:
        TickData list, oldest first
    """
    rows = frame[frame['timestamp'] >= since]
    return [
        TickData(timestamp=ts.to_pydatetime(), symbol=SYMBOL, bid=float(price),
                 ask=float(price), volume=float(volume), time_msc=int(msc), last=float(price))
        for ts, price, volume, msc in zip(
            rows['timestamp'], rows['price'], rows['volume'], rows['time_msc'])
    ]


def render_archive(
    frame: pd.DataFrame,
    directory: Path,
    timeframes: Sequence[str],
) -> Dict[str, Path]:
    """
    Render the stream into bar files the way an import does, one parquet file per timeframe.

    Args:
        frame: The tick frame
        directory: Where to write the files
        timeframes: Timeframes to keep

    Returns:
        Bar file per timeframe
    """
    rendered = VectorizedBarRenderer(SYMBOL, log_buffer=[]).render_all_timeframes(frame)
    files = {}
    for timeframe in timeframes:
        path = directory / f'{SYMBOL}_{timeframe}.parquet'
        rendered[timeframe].to_parquet(path)
        files[timeframe] = path
    return files


class ArchiveIndexStub:
    """
    Stands in for the bar index: answers with the synthetic archive's files.

    Args:
        files: Bar file per timeframe
    """

    def __init__(self, files: Dict[str, Path]):
        self._files = files

    def get_bar_file(self, broker_type: str, symbol: str, timeframe: str) -> Optional[Path]:
        return self._files.get(timeframe)

    def get_available_timeframes(self, broker_type: str, symbol: str) -> List[str]:
        return sorted(self._files)


def requirements_for(required: Dict[str, int], start: datetime) -> List[BarRequirement]:
    """
    The bar requirements the batch derives for one scenario.

    Args:
        required: Bars required per timeframe
        start: The scenario's configured start

    Returns:
        One requirement per timeframe
    """
    return [
        BarRequirement(scenario_name=SCENARIO, broker_type=BROKER_TYPE, symbol=SYMBOL,
                       timeframe=timeframe, warmup_count=count, start_time=start)
        for timeframe, count in required.items()
    ]


def archive_package(
    preparator,
    required: Dict[str, int],
    start: datetime,
    first_tick: datetime,
) -> ProcessDataPackage:
    """
    The package the batch hands a backtest or a mock session, selected by the batch's own code.

    Args:
        preparator: A SharedDataPreparator whose bar index points at the synthetic archive
        required: Bars required per timeframe
        start: The scenario's configured start
        first_tick: The scenario's first tick

    Returns:
        The scenario's package (warmup bars only)
    """
    bars, counts, references = preparator.prepare_bars(
        requirements_for(required, start), {SCENARIO: first_tick})
    return ProcessDataPackage(
        ticks={},
        bars={timeframe: rows for (_, timeframe), rows in bars.items()},
        broker_configs=(),
        bar_counts={timeframe: count for (_, timeframe), count in counts.items()},
        warmup_reference_time=references[SCENARIO],
    )


class VenueStub:
    """
    Kraken's OHLC endpoint over the synthetic stream, at an instant the test moves.

    Rows carry the period's OPENING time and the venue's string prices; the row of the period
    containing `now` is the one still forming. `last` names the newest committed period: by
    default the period before the forming one, or `committed_through` to model the commit lag
    (measured: a period is committed 0.8 s after it ends on 2026-10-12, 1.5–2.2 s on 2026-10-13).

    Args:
        frame: The tick frame the venue traded
    """

    def __init__(self, frame: pd.DataFrame):
        self._frame = frame
        self.now: Optional[datetime] = None
        self.committed_through: Optional[datetime] = None
        self.calls: List[datetime] = []
        self.failing = False

    def answer(self, pair: str, interval: int, since: int) -> Dict:
        """
        Stand-in for KrakenOhlcBarFetcher._fetch_ohlc.

        Args:
            pair: Venue pair name
            interval: Interval in minutes
            since: Unix timestamp of the oldest row wanted

        Returns:
            The `result` object of the venue's answer
        """
        if self.failing:
            raise ConnectionError('Kraken OHLC unreachable (test)')
        self.calls.append(self.now)
        timeframe = next(
            tf for tf in TimeframeConfig.sorted() if TimeframeConfig.get_minutes(tf) == interval)
        traded = self._frame[self._frame['timestamp'] <= self.now]
        periods = traded['timestamp'].dt.floor(f'{interval}min')
        rows = []
        for opened, group in traded.groupby(periods):
            if opened.timestamp() < since:
                continue
            price = group['price']
            rows.append([
                int(opened.timestamp()), str(float(price.iloc[0])), str(float(price.max())),
                str(float(price.min())), str(float(price.iloc[-1])), str(float(price.mean())),
                str(float(group['volume'].sum())), int(len(group)),
            ])
        forming = period_start(self.now, timeframe)
        committed = self.committed_through or forming - timedelta(minutes=interval)
        return {pair: rows[-720:], 'last': int(committed.timestamp())}


class RequirementWorker:
    """
    A worker surface that only states its bar requirement — what the warmup path reads.

    Args:
        required: Bars required per timeframe
    """

    def __init__(self, required: Dict[str, int]):
        self._required = required

    def get_warmup_requirements(self) -> Dict[str, int]:
        return dict(self._required)

    def get_required_timeframes(self) -> List[str]:
        return list(self._required)


def session_config(adapter_type: str, bar_max_history: int = 1000) -> SimpleNamespace:
    """
    The parts of an AutoTrader profile the warmup path reads.

    Args:
        adapter_type: 'live' or 'mock'
        bar_max_history: The renderer's history capacity

    Returns:
        A config stand-in
    """
    return SimpleNamespace(
        symbol=SYMBOL, adapter_type=adapter_type,
        execution=SimpleNamespace(bar_max_history=bar_max_history))


def history_rows(bars) -> List[tuple]:
    """
    A history as comparable tuples.

    Args:
        bars: Bars, oldest first

    Returns:
        (open time, open, high, low, close, volume, tick count) per bar
    """
    return [(bar.timestamp, bar.open, bar.high, bar.low, bar.close, round(bar.volume, 9),
             bar.tick_count) for bar in bars]
