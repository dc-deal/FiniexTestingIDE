"""
Tick Parity — Each Pipeline Obtains Its Ticks the Way It Really Does

The bar parity tests feed both pipelines one synthetic tick list, so they compare pipeline against
pipeline over identical input. What they cannot see is the one place the two runs differ by
design: where their ticks come from. A live session reads Kraken's WebSocket through the message
parser; a backtest reads the collector's file through the importer, the tick reader and the
process transport. A field one route fills and the other does not reaches a strategy as a
difference no bar-parity test notices.

So this test takes real Kraken v2 frames, recorded verbatim (the tick-source fixture), and sends
them down both routes:

- **live:** the frames through `KrakenTickMessageParser`;
- **archive:** the same events as the collector's format 1.7.0 writes them — one row per trade,
  the quote standing at it, the trade price as `last`, a trade before the first quote as a flat
  row (bid = ask = last, no quote age) — through the importer, `read_tick_parquet`, the transport
  and its deserializer.

The recorded frames span ten seconds; they are replayed at four minute offsets, prices moved by a
few dollars a minute, so the two M1 bar histories have bars to compare. The archive row is our
model of the collector's format, built from the same recorded events.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

from python.data_management.importers.tick_data_importer import TickDataImporter
from python.framework.autotrader.tick_sources.kraken_tick_message_parser import (
    KrakenTickMessageParser,
)
from python.framework.data_preparation.tick_parquet_reader import read_tick_parquet
from python.framework.types.market_types.market_data_types import TickData
from python.framework.utils.process_serialization_utils import (
    process_deserialize_ticks_batch,
    serialize_ticks_for_transport,
)
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    find_tick_parquets,
    write_json_fixture,
)
from tests.shared.parity_comparators import assert_bars_equal
from tests.shared.parity_runners import run_autotrader_bars, run_simulation_bars

SYMBOL = 'BTCUSD'
TIMEFRAME = 'M1'
_FRAMES = (Path(__file__).resolve().parents[1] / 'fixtures' / 'tick_sources'
           / 'kraken_ws_frames.json')
# The recorded order: a trade before any quote, then quote and trades alternating
_SEQUENCE = ('trade_sell', 'ticker_snapshot', 'trade_buy', 'ticker_update', 'trade_burst')
_MINUTES = 4
_PRICE_STEP = 5.0
# A 1.7.0 file states who wrote it; the importer refuses one that does not
_ORIGIN = {'origin': {'instance_id': 'c3d4e5f60718', 'collected_on': 'test-host',
                      'producer': 'finiex-data-collector', 'producer_version': '1.7.0'}}
# The collector's arrival stamp a fixed moment after the venue's event time
_ARRIVAL_DELAY_MS = 40


def _parse_time(text: str) -> datetime:
    """
    Read a Kraken time stamp.

    Args:
        text: ISO 8601 with a trailing Z

    Returns:
        The UTC instant
    """
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _replayed_frames() -> List[Dict[str, Any]]:
    """
    The recorded frames, replayed at minute offsets with their prices and trade ids moved.

    Returns:
        The frames in the order they arrive
    """
    recorded = json.loads(_FRAMES.read_text())
    frames: List[Dict[str, Any]] = []
    for minute in range(_MINUTES):
        shift = timedelta(minutes=minute)
        step = minute * _PRICE_STEP
        for key in _SEQUENCE:
            frame = json.loads(json.dumps(recorded[key]))
            for entry in frame['data']:
                entry['timestamp'] = (_parse_time(entry['timestamp']) + shift).isoformat().replace(
                    '+00:00', 'Z')
                for field in ('price', 'bid', 'ask', 'last'):
                    if field in entry:
                        entry[field] = round(entry[field] + step, 1)
                if 'trade_id' in entry:
                    entry['trade_id'] += minute * 100
            frames.append(frame)
    return frames


def _live_ticks(frames: List[Dict[str, Any]]) -> List[TickData]:
    """
    The live route: every frame through the message parser.

    Args:
        frames: The frames in arrival order

    Returns:
        The ticks the parser produced
    """
    parser = KrakenTickMessageParser(SYMBOL)
    ticks: List[TickData] = []
    for frame in frames:
        parsed = parser.parse_message(json.dumps(frame))
        if parsed:
            ticks.extend(parsed)
    return ticks


def _collector_rows(frames: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    The same events as the collector's format 1.7.0 writes them.

    Args:
        frames: The frames in arrival order

    Returns:
        One tick row per trade
    """
    rows: List[Dict[str, Any]] = []
    quote: Optional[Tuple[float, float, datetime]] = None
    for frame in frames:
        if frame['channel'] == 'ticker':
            entry = frame['data'][0]
            quote = (entry['bid'], entry['ask'], _parse_time(entry['timestamp']))
            continue
        for trade in frame['data']:
            at = _parse_time(trade['timestamp'])
            time_msc = int(at.timestamp() * 1000)
            price = trade['price']
            bid, ask = (quote[0], quote[1]) if quote is not None else (price, price)
            rows.append({
                'timestamp': at.strftime('%Y.%m.%d %H:%M:%S'),
                'time_msc': time_msc,
                'bid': bid,
                'ask': ask,
                'last': price,
                'tick_volume': 0,
                'real_volume': trade['qty'],
                'chart_tick_volume': 1,
                'spread_points': round((ask - bid) / 0.1),
                'spread_pct': round((ask - bid) / bid * 100, 6),
                'tick_flags': trade['side'].upper(),
                'session': '24h',
                'collected_msc': time_msc + _ARRIVAL_DELAY_MS,
                'trade_id': trade['trade_id'],
                'quote_age_ms': (int((at - quote[2]).total_seconds() * 1000)
                                 if quote is not None else None),
            })
    return rows


def _archive_ticks(rows: List[Dict[str, Any]], tmp_path: Path) -> List[TickData]:
    """
    The archive route: a collector file through the importer, the reader and the transport.

    Args:
        rows: The collector's tick rows
        tmp_path: Where the file and its parquet go

    Returns:
        The ticks a backtest's event loop receives
    """
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    first = datetime.fromtimestamp(rows[0]['time_msc'] / 1000, tz=timezone.utc)
    payload = build_minimal_tick_json(
        symbol=SYMBOL, broker='Kraken', broker_type='kraken_spot',
        start_time=first.strftime('%Y.%m.%d %H:%M:%S'), tick_count=0,
        data_format_version='1.7.0', extra_metadata=_ORIGIN, custom_ticks=rows)
    write_json_fixture(source, f'{SYMBOL}_{first:%Y%m%d_%H%M%S}_ticks.json', payload)
    TickDataImporter(source_dir=str(source), target_dir=str(target),
                     auto_render_bars=False).process_all_exports()
    (parquet,) = find_tick_parquets(target)
    transport = serialize_ticks_for_transport(read_tick_parquet(parquet))
    return list(process_deserialize_ticks_batch(SYMBOL, {SYMBOL: tuple(transport)}))


@pytest.fixture(scope='module')
def routes(tmp_path_factory) -> Tuple[List[TickData], List[TickData]]:
    """
    Both routes' ticks over the same recorded events.

    Args:
        tmp_path_factory: pytest's factory for a module-scoped folder

    Returns:
        (live ticks, archive ticks)
    """
    frames = _replayed_frames()
    live = _live_ticks(frames)
    archive = _archive_ticks(_collector_rows(frames), tmp_path_factory.mktemp('tick_routes'))
    return live, archive


class TestBothRoutesDeliverTheSameTicks:
    """
    Tick by tick, what a strategy reads is the same on both routes.
    """

    def test_every_trade_arrives_on_both_routes(self, routes):
        live, archive = routes
        assert live and len(live) == len(archive)

    def test_prices_quotes_and_volumes_agree(self, routes):
        live, archive = routes
        for index, (on_live, on_archive) in enumerate(zip(live, archive)):
            for field in ('time_msc', 'bid', 'ask', 'last', 'price', 'mid', 'volume'):
                assert getattr(on_live, field) == getattr(on_archive, field), (
                    f'tick {index} {field}: live {getattr(on_live, field)}, '
                    f'archive {getattr(on_archive, field)}')

    def test_a_trade_before_the_first_quote_is_flat_on_both_routes(self, routes):
        live, archive = routes
        for tick in (live[0], archive[0]):
            assert tick.bid == tick.ask == tick.last == tick.price

    def test_a_trade_after_a_quote_carries_it_on_both_routes(self, routes):
        live, archive = routes
        for tick in (live[1], archive[1]):
            assert tick.bid < tick.ask
            assert tick.price == tick.last

    def test_the_event_time_agrees_to_the_millisecond(self, routes):
        # Live keeps the venue's microseconds, the archive rebuilds the time from its
        # millisecond stamp: the two differ below one millisecond, and only there
        live, archive = routes
        for on_live, on_archive in zip(live, archive):
            assert on_archive.timestamp == datetime.fromtimestamp(
                on_live.time_msc / 1000, tz=timezone.utc)
            assert timedelta(0) <= on_live.timestamp - on_archive.timestamp < timedelta(
                milliseconds=1)


class TestBothRoutesRenderTheSameBars:
    """
    Each pipeline renders the ticks its own route delivered.
    """

    def test_the_m1_bars_are_identical(self, routes):
        live, archive = routes
        sim_controller = run_simulation_bars(archive, SYMBOL, TIMEFRAME)
        at_controller = run_autotrader_bars(live, SYMBOL, 'BTC', TIMEFRAME)
        assert len(sim_controller.get_bar_history(SYMBOL, TIMEFRAME)) == _MINUTES - 1
        assert_bars_equal(sim_controller, at_controller, SYMBOL, TIMEFRAME)
