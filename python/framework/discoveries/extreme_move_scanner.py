"""
Extreme Move Scanner
====================
Scans bar data for extreme directional price movements (strong LONG/SHORT trends).

Uses ATR-based normalization for cross-instrument comparability.
Sliding window approach over multiple configurable window sizes.

Location: python/framework/discoveries/extreme_move_scanner.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.discoveries.discovery_types import (
    ExtremeMove,
    ExtremeMoveResult,
    MoveDirection,
)
from python.framework.factory.broker_config_factory import BrokerConfigFactory
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.broker_types import SymbolSpecification

vLog = get_global_logger()

_DEFAULT_CONFIG_PATH = "configs/discoveries/extreme_moves_config.json"


class ExtremeMoveScanner:
    """
    Scans bar data for extreme directional price movements.

    Detection approach:
    1. Calculate ATR over the full bar dataset (rolling EMA)
    2. For each configured window size, slide across the bars
    3. Measure max directional move as ATR multiple
    4. Filter by min_atr_multiple threshold
    5. Return top N moves per direction, sorted by strength
    """

    def __init__(
        self,
        config_path: str = _DEFAULT_CONFIG_PATH,
        logger: AbstractLogger = vLog
    ):
        self._logger = logger
        self._config = self._load_config(config_path)
        self._bar_index: Optional[BarsIndexManager] = None
        self._symbol_specs: Dict[str, SymbolSpecification] = {}
        self._loaded_broker_types: set = set()
        self._market_config = MarketConfigManager()

    def _load_config(self, config_path: str) -> dict:
        """Load extreme moves configuration."""
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")

        with open(path, 'r') as f:
            raw = json.load(f)

        return raw.get('extreme_moves', raw)

    def _get_bar_index(self) -> BarsIndexManager:
        """Lazy-load bar index."""
        if self._bar_index is None:
            self._bar_index = BarsIndexManager(logger=self._logger)
            self._bar_index.build_index()
        return self._bar_index

    def _load_symbol_spec(self, broker_type: str, symbol: str) -> Optional[SymbolSpecification]:
        """Load symbol specification from broker config."""
        if symbol in self._symbol_specs:
            return self._symbol_specs[symbol]

        if broker_type not in self._loaded_broker_types:
            try:
                broker_path = self._market_config.get_broker_config_path(broker_type)
                broker_config = BrokerConfigFactory.build_broker_config(broker_path)
                for sym in broker_config.get_all_aviable_symbols():
                    try:
                        spec = broker_config.get_symbol_specification(sym)
                        self._symbol_specs[sym] = spec
                    except Exception:
                        pass
                self._loaded_broker_types.add(broker_type)
            except Exception as e:
                self._logger.warning(f"Failed to load broker config for {broker_type}: {e}")

        return self._symbol_specs.get(symbol)

    def _get_pip_size(self, spec: SymbolSpecification) -> float:
        """Calculate pip size from symbol specification."""
        if spec.digits == 5 or spec.digits == 3:
            return spec.tick_size * 10
        return spec.tick_size

    def _load_and_prepare_bars(
        self,
        broker_type: str,
        symbol: str,
        timeframe: str
    ) -> pd.DataFrame:
        """Load bars and calculate ATR."""
        bar_index = self._get_bar_index()
        bar_file = bar_index.get_bar_file(broker_type, symbol, timeframe)
        if not bar_file:
            raise ValueError(f"No bar data found for {broker_type}/{symbol} {timeframe}")

        df = pd.read_parquet(bar_file)

        # Ensure timestamp is datetime with UTC
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        if df['timestamp'].dt.tz is None:
            df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')
        else:
            df['timestamp'] = df['timestamp'].dt.tz_convert('UTC')

        df = df.sort_values('timestamp').reset_index(drop=True)

        if 'bar_type' not in df.columns:
            df['bar_type'] = 'real'
        if 'tick_count' not in df.columns:
            df['tick_count'] = 1

        # ATR calculation
        atr_period = self._config.get('atr_period', 14)
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift(1))
        low_close = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['atr'] = tr.ewm(span=atr_period, adjust=False).mean()

        return df

    # =========================================================================
    # CORE SCANNING
    # =========================================================================

    def scan(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None
    ) -> ExtremeMoveResult:
        """
        Scan for extreme directional moves in bar data.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe override (default from config)

        Returns:
            ExtremeMoveResult with top LONG and SHORT extreme moves
        """
        tf = timeframe or self._config.get('timeframe', 'M5')
        spec = self._load_symbol_spec(broker_type, symbol)
        if not spec:
            raise ValueError(f"No symbol specification found for {broker_type}/{symbol}")

        pip_size = self._get_pip_size(spec)
        df = self._load_and_prepare_bars(broker_type, symbol, tf)

        avg_atr = float(df['atr'].mean())
        min_atr_multiple = self._config.get('min_atr_multiple', 3.0)
        max_adverse_atr = self._config.get('max_adverse_atr_multiple', 1.5)
        min_real_bar_ratio = self._config.get('min_real_bar_ratio', 0.5)
        window_sizes = self._config.get('window_sizes', [200, 500, 1000, 2000])
        top_n = self._config.get('top_n', 10)

        all_longs: List[ExtremeMove] = []
        all_shorts: List[ExtremeMove] = []

        # Precompute arrays for performance
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        timestamps = df['timestamp'].values
        atrs = df['atr'].values
        tick_counts = df['tick_count'].values
        bar_types = df['bar_type'].values if 'bar_type' in df.columns else None

        total_bars = len(df)

        for window_size in window_sizes:
            if window_size > total_bars:
                continue

            # Slide with step = window_size // 4 for overlap
            step = max(1, window_size // 4)

            for start_idx in range(0, total_bars - window_size + 1, step):
                end_idx = start_idx + window_size

                # Check real bar ratio
                if bar_types is not None:
                    real_count = np.sum(bar_types[start_idx:end_idx] == 'real')
                    if real_count / window_size < min_real_bar_ratio:
                        continue

                window_atr = float(np.mean(atrs[start_idx:end_idx]))
                if window_atr <= 0:
                    continue

                entry_price = float(opens[start_idx])
                window_high = float(np.max(highs[start_idx:end_idx]))
                window_low = float(np.min(lows[start_idx:end_idx]))
                exit_price = float(closes[end_idx - 1])
                window_ticks = int(np.sum(tick_counts[start_idx:end_idx]))

                # LONG: measure upward move from entry to highest high
                long_move = window_high - entry_price
                long_move_atr = long_move / window_atr
                long_adverse = entry_price - window_low  # max drawdown for a LONG

                if long_move_atr >= min_atr_multiple:
                    long_adverse_atr = long_adverse / window_atr
                    if long_adverse_atr <= max_adverse_atr:
                        all_longs.append(ExtremeMove(
                            broker_type=broker_type,
                            symbol=symbol,
                            timeframe=tf,
                            direction=MoveDirection.LONG,
                            start_time=pd.Timestamp(timestamps[start_idx]).to_pydatetime(),
                            end_time=pd.Timestamp(timestamps[end_idx - 1]).to_pydatetime(),
                            bar_count=window_size,
                            entry_price=entry_price,
                            extreme_price=window_high,
                            exit_price=exit_price,
                            move_pips=round(long_move / pip_size, 1),
                            move_atr_multiple=round(long_move_atr, 2),
                            max_adverse_pips=round(long_adverse / pip_size, 1),
                            window_atr=round(window_atr, spec.digits),
                            tick_count=window_ticks,
                        ))

                # SHORT: measure downward move from entry to lowest low
                short_move = entry_price - window_low
                short_move_atr = short_move / window_atr
                short_adverse = window_high - entry_price  # max adverse for a SHORT

                if short_move_atr >= min_atr_multiple:
                    short_adverse_atr = short_adverse / window_atr
                    if short_adverse_atr <= max_adverse_atr:
                        all_shorts.append(ExtremeMove(
                            broker_type=broker_type,
                            symbol=symbol,
                            timeframe=tf,
                            direction=MoveDirection.SHORT,
                            start_time=pd.Timestamp(timestamps[start_idx]).to_pydatetime(),
                            end_time=pd.Timestamp(timestamps[end_idx - 1]).to_pydatetime(),
                            bar_count=window_size,
                            entry_price=entry_price,
                            extreme_price=window_low,
                            exit_price=exit_price,
                            move_pips=round(short_move / pip_size, 1),
                            move_atr_multiple=round(short_move_atr, 2),
                            max_adverse_pips=round(short_adverse / pip_size, 1),
                            window_atr=round(window_atr, spec.digits),
                            tick_count=window_ticks,
                        ))

        # Deduplicate overlapping windows: keep strongest per cluster
        all_longs = self._deduplicate_moves(all_longs)
        all_shorts = self._deduplicate_moves(all_shorts)

        # Sort by ATR multiple (strongest first) and take top N
        all_longs.sort(key=lambda m: m.move_atr_multiple, reverse=True)
        all_shorts.sort(key=lambda m: m.move_atr_multiple, reverse=True)

        self._logger.info(
            f"Scanned {total_bars} bars for {broker_type}/{symbol}: "
            f"found {len(all_longs)} LONG, {len(all_shorts)} SHORT extreme moves"
        )

        return ExtremeMoveResult(
            broker_type=broker_type,
            symbol=symbol,
            timeframe=tf,
            longs=all_longs[:top_n],
            shorts=all_shorts[:top_n],
            scanned_bars=total_bars,
            avg_atr=round(avg_atr, spec.digits),
            pip_size=pip_size,
            generated_at=datetime.now(timezone.utc),
        )

    def _deduplicate_moves(self, moves: List[ExtremeMove]) -> List[ExtremeMove]:
        """
        Remove overlapping windows, keeping the strongest per cluster.

        Two moves overlap if their time ranges share more than 50% of bars.
        """
        if not moves:
            return []

        # Sort by strength descending
        moves.sort(key=lambda m: m.move_atr_multiple, reverse=True)

        kept: List[ExtremeMove] = []
        for candidate in moves:
            overlaps = False
            for existing in kept:
                # Check time overlap
                overlap_start = max(candidate.start_time, existing.start_time)
                overlap_end = min(candidate.end_time, existing.end_time)
                if overlap_start < overlap_end:
                    overlap_bars = (overlap_end - overlap_start).total_seconds()
                    candidate_bars = (candidate.end_time - candidate.start_time).total_seconds()
                    if candidate_bars > 0 and overlap_bars / candidate_bars > 0.5:
                        overlaps = True
                        break
            if not overlaps:
                kept.append(candidate)

        return kept

    # =========================================================================
    # CONSOLE OUTPUT
    # =========================================================================

    def scan_and_print(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None,
        top_n: int = 10
    ) -> ExtremeMoveResult:
        """
        Scan for extreme moves and print formatted report.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Timeframe override
            top_n: Number of top results per direction

        Returns:
            ExtremeMoveResult
        """
        result = self.scan(broker_type, symbol, timeframe)
        self.print_result(result, top_n)
        return result

    @staticmethod
    def print_result(result: ExtremeMoveResult, top_n: int = 10) -> None:
        """
        Print formatted extreme move report from a result.

        Args:
            result: ExtremeMoveResult (from scan or cache)
            top_n: Number of top results per direction
        """
        print("\n" + "=" * 130)
        print(f"EXTREME MOVE DISCOVERY: {result.symbol}")
        print("=" * 130)
        print(f"Data Source:    {result.broker_type}")
        print(f"Timeframe:      {result.timeframe}")
        print(f"Bars Scanned:   {result.scanned_bars:,}")
        print(f"Avg ATR:        {result.avg_atr}")
        print(f"Pip Size:       {result.pip_size}")

        pip = result.pip_size

        header = (
            f"{'#':>3}  {'ATR Mult':>8}  {'Pips':>8}  {'Adverse':>8}  "
            f"{'Entry':>10}  {'Extreme':>10}  {'Adverse@':>10}  {'Exit':>10}  "
            f"{'W-ATR':>7}  {'Bars':>6}  {'Ticks':>8}  {'Start':>20}  {'End':>20}"
        )

        # LONG moves
        print("\n" + "-" * 150)
        print(f"LONG Extreme Moves (top {top_n})")
        print("-" * 150)
        if result.longs:
            print(header)
            for i, move in enumerate(result.longs[:top_n], 1):
                adverse_price = move.entry_price - move.max_adverse_pips * pip
                print(
                    f"{i:>3}  {move.move_atr_multiple:>8.2f}  "
                    f"{move.move_pips:>8.1f}  {move.max_adverse_pips:>8.1f}  "
                    f"{move.entry_price:>10.3f}  {move.extreme_price:>10.3f}  "
                    f"{adverse_price:>10.3f}  {move.exit_price:>10.3f}  "
                    f"{move.window_atr:>7.3f}  "
                    f"{move.bar_count:>6}  {move.tick_count:>8}  "
                    f"{move.start_time.strftime('%Y-%m-%d %H:%M'):>20}  "
                    f"{move.end_time.strftime('%Y-%m-%d %H:%M'):>20}"
                )
        else:
            print("   No extreme LONG moves found")

        # SHORT moves
        print("\n" + "-" * 150)
        print(f"SHORT Extreme Moves (top {top_n})")
        print("-" * 150)
        if result.shorts:
            print(header)
            for i, move in enumerate(result.shorts[:top_n], 1):
                adverse_price = move.entry_price + move.max_adverse_pips * pip
                print(
                    f"{i:>3}  {move.move_atr_multiple:>8.2f}  "
                    f"{move.move_pips:>8.1f}  {move.max_adverse_pips:>8.1f}  "
                    f"{move.entry_price:>10.3f}  {move.extreme_price:>10.3f}  "
                    f"{adverse_price:>10.3f}  {move.exit_price:>10.3f}  "
                    f"{move.window_atr:>7.3f}  "
                    f"{move.bar_count:>6}  {move.tick_count:>8}  "
                    f"{move.start_time.strftime('%Y-%m-%d %H:%M'):>20}  "
                    f"{move.end_time.strftime('%Y-%m-%d %H:%M'):>20}"
                )
        else:
            print("   No extreme SHORT moves found")

        print("=" * 150 + "\n")
