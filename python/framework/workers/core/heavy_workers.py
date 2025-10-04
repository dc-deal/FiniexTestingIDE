"""
FiniexTestingIDE - Heavy Workers für Parallelisierungs-Tests
Workers mit künstlicher CPU-Last zum Testen von Parallel-Performance

REFACTORED (Issue 2): Updated contracts with required_parameters
and factory-compatible structure.
"""

import time
from typing import Dict, List

import numpy as np

from python.framework.types import Bar, TickData, WorkerContract, WorkerResult, WorkerType
from python.framework.workers.abstract_blackbox_worker import \
    AbstractBlackboxWorker


class HeavyRSIWorker(AbstractBlackboxWorker):
    """
    RSI Worker mit künstlicher CPU-Last.
    Simuliert komplexe Berechnungen (z.B. ML-Model, FFT, etc.)
    """

    def __init__(self, name: str = "HeavyRSI", parameters: Dict = None, **kwargs):
        """
        Args:
            name: Worker name
            parameters: Factory-style parameters dict
            **kwargs: Legacy constructor support

        Required parameters:
            - period: RSI calculation period
            - timeframe: Timeframe to use

        Optional parameters:
            - artificial_load_ms: CPU load duration (default: 5.0ms)
        """
        super().__init__(name, parameters)

        # Extract parameters
        params = parameters or {}
        self.period = params.get('period') or kwargs.get('period', 14)
        self.timeframe = params.get(
            'timeframe') or kwargs.get('timeframe', 'M5')
        self.artificial_load_ms = params.get(
            'artificial_load_ms') or kwargs.get('artificial_load_ms', 5.0)

    def get_contract(self) -> WorkerContract:
        """
        Define Heavy RSI worker contract.

        REFACTORED (Issue 2): Split into required/optional parameters.
        """
        return WorkerContract(
            # ============================================
            # NEW (Issue 2): Factory-Compatible Contract
            # ============================================
            worker_type=WorkerType.COMPUTE,

            # Required parameters - MUST be provided
            required_parameters={
                'period': int,      # RSI period
                'timeframe': str,   # Timeframe
            },

            # Optional parameters - have defaults
            optional_parameters={
                'artificial_load_ms': 5.0,  # CPU load duration
            },

            # ============================================
            # Existing contract fields (unchanged)
            # ============================================
            parameters={
                "rsi_period": self.period,
                "rsi_timeframe": self.timeframe,
                "artificial_load_ms": self.artificial_load_ms,
            },
            price_change_sensitivity=0.0001,
            max_computation_time_ms=self.artificial_load_ms + 10.0,
            required_timeframes=self.get_required_timeframes(),
            warmup_requirements=self.get_warmup_requirements(),
        )

    def get_warmup_requirements(self):
        requirements = {}
        minimum_warmup_bars = self.period + 10
        for tf in self.get_required_timeframes():
            requirements[tf] = minimum_warmup_bars
        return requirements

    def get_required_timeframes(self) -> List[str]:
        return [self.timeframe]

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """RSI computation mit künstlicher CPU-Last"""

        # === KÜNSTLICHE LAST (CPU-intensive) ===
        self._simulate_heavy_computation()

        # === NORMALE RSI BERECHNUNG ===
        bars = bar_history.get(self.timeframe, [])

        if len(bars) < self.period + 1:
            return WorkerResult(
                worker_name=self.name,
                value=50.0,
                confidence=0.0,
                metadata={"insufficient_bars": True},
            )

        close_prices = np.array(
            [bar.close for bar in bars[-(self.period + 1):]])
        deltas = np.diff(close_prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        confidence = min(1.0, len(bars) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value=float(rsi),
            confidence=confidence,
            metadata={
                "artificial_load_ms": self.artificial_load_ms,
                "period": self.period,
            },
        )

    def _simulate_heavy_computation(self):
        """Simuliert CPU-intensive Berechnungen (Matrix ops)"""
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0

        size = 50
        while (time.perf_counter() - start) < target_duration:
            matrix_a = np.random.rand(size, size)
            matrix_b = np.random.rand(size, size)
            result = np.dot(matrix_a, matrix_b)
            result = np.sin(result) + np.cos(result)
            _ = result.sum()


class HeavyEnvelopeWorker(AbstractBlackboxWorker):
    """
    Envelope Worker mit künstlicher CPU-Last.
    Simuliert komplexe Band-Berechnungen.
    """

    def __init__(self, name: str = "HeavyEnvelope", parameters: Dict = None, **kwargs):
        """
        Args:
            name: Worker name
            parameters: Factory-style parameters dict
            **kwargs: Legacy constructor support

        Optional parameters (all have defaults):
            - period: MA period (default: 20)
            - deviation: Band deviation (default: 0.02)
            - timeframe: Timeframe (default: "M5")
            - artificial_load_ms: CPU load (default: 8.0ms)
        """
        super().__init__(name, parameters)

        params = parameters or {}
        self.period = params.get('period') or kwargs.get('period', 20)
        self.deviation = params.get(
            'deviation') or kwargs.get('deviation', 0.02)
        self.timeframe = params.get(
            'timeframe') or kwargs.get('timeframe', 'M5')
        self.artificial_load_ms = params.get(
            'artificial_load_ms') or kwargs.get('artificial_load_ms', 8.0)

    def get_contract(self) -> WorkerContract:
        """
        Define Heavy Envelope worker contract.

        REFACTORED (Issue 2): All parameters optional with defaults.
        """
        return WorkerContract(
            # ============================================
            # NEW (Issue 2): Factory-Compatible Contract
            # ============================================
            worker_type=WorkerType.COMPUTE,

            # Required parameters - NONE (all have defaults)
            required_parameters={},

            # Optional parameters - all configurable
            optional_parameters={
                'period': 20,
                'deviation': 0.02,
                'timeframe': 'M5',
                'artificial_load_ms': 8.0,  # Higher load than RSI
            },

            # ============================================
            # Existing contract fields (unchanged)
            # ============================================
            parameters={
                "envelope_period": self.period,
                "envelope_deviation": self.deviation,
                "envelope_timeframe": self.timeframe,
                "artificial_load_ms": self.artificial_load_ms,
            },
            price_change_sensitivity=0.0001,
            max_computation_time_ms=self.artificial_load_ms + 10.0,
            required_timeframes=self.get_required_timeframes(),
            warmup_requirements=self.get_warmup_requirements(),
        )

    def get_warmup_requirements(self):
        requirements = {}
        minimum_warmup_bars = self.period + 10
        for tf in self.get_required_timeframes():
            requirements[tf] = minimum_warmup_bars
        return requirements

    def get_required_timeframes(self) -> List[str]:
        return [self.timeframe]

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """Envelope computation mit künstlicher CPU-Last"""

        # === KÜNSTLICHE LAST (FFT-like) ===
        self._simulate_heavy_computation()

        # === NORMALE ENVELOPE BERECHNUNG ===
        bars = bar_history.get(self.timeframe, [])

        if len(bars) < self.period:
            return WorkerResult(
                worker_name=self.name,
                value={"upper": 0, "middle": 0, "lower": 0},
                confidence=0.0,
            )

        close_prices = np.array([bar.close for bar in bars[-self.period:]])
        middle = np.mean(close_prices)
        std_dev = np.std(close_prices)

        upper = middle + (std_dev * self.deviation)
        lower = middle - (std_dev * self.deviation)

        current_price = tick.mid
        position = 0.5

        if upper != lower:
            position = (current_price - lower) / (upper - lower)
            position = max(0.0, min(1.0, position))

        confidence = min(1.0, len(bars) / (self.period * 2))

        return WorkerResult(
            worker_name=self.name,
            value={
                "upper": float(upper),
                "middle": float(middle),
                "lower": float(lower),
                "position": float(position),
            },
            confidence=confidence,
            metadata={
                "artificial_load_ms": self.artificial_load_ms,
                "period": self.period,
            },
        )

    def _simulate_heavy_computation(self):
        """Simuliert FFT-like computations"""
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0

        size = 1000
        while (time.perf_counter() - start) < target_duration:
            data = np.random.rand(size)
            fft_result = np.fft.fft(data)
            inverse = np.fft.ifft(fft_result)
            kernel = np.random.rand(10)
            conv_result = np.convolve(data[:100], kernel, mode='same')
            _ = conv_result.sum() + inverse.real.sum()


class HeavyMACDWorker(AbstractBlackboxWorker):
    """
    MACD Worker mit künstlicher CPU-Last.
    Zusätzlicher Worker für bessere Parallelisierungs-Tests.
    """

    def __init__(self, name: str = "HeavyMACD", parameters: Dict = None, **kwargs):
        """
        Args:
            name: Worker name
            parameters: Factory-style parameters dict
            **kwargs: Legacy constructor support

        Optional parameters:
            - fast: Fast EMA period (default: 12)
            - slow: Slow EMA period (default: 26)
            - signal: Signal line period (default: 9)
            - timeframe: Timeframe (default: "M5")
            - artificial_load_ms: CPU load (default: 6.0ms)
        """
        super().__init__(name, parameters)

        params = parameters or {}
        self.fast = params.get('fast') or kwargs.get('fast', 12)
        self.slow = params.get('slow') or kwargs.get('slow', 26)
        self.signal = params.get('signal') or kwargs.get('signal', 9)
        self.timeframe = params.get(
            'timeframe') or kwargs.get('timeframe', 'M5')
        self.artificial_load_ms = params.get(
            'artificial_load_ms') or kwargs.get('artificial_load_ms', 6.0)

    def get_contract(self) -> WorkerContract:
        """
        Define Heavy MACD worker contract.

        REFACTORED (Issue 2): All parameters optional.
        """
        return WorkerContract(
            # ============================================
            # NEW (Issue 2): Factory-Compatible Contract
            # ============================================
            worker_type=WorkerType.COMPUTE,

            # Required parameters - NONE
            required_parameters={},

            # Optional parameters
            optional_parameters={
                'fast': 12,
                'slow': 26,
                'signal': 9,
                'timeframe': 'M5',
                'artificial_load_ms': 6.0,
            },

            # ============================================
            # Existing contract fields (unchanged)
            # ============================================
            parameters={
                "macd_fast": self.fast,
                "macd_slow": self.slow,
                "macd_signal": self.signal,
                "artificial_load_ms": self.artificial_load_ms,
            },
            price_change_sensitivity=0.0001,
            max_computation_time_ms=self.artificial_load_ms + 10.0,
            required_timeframes=[self.timeframe],
            warmup_requirements={self.timeframe: self.slow + 10},
        )

    def get_required_timeframes(self) -> List[str]:
        return [self.timeframe]

    def get_warmup_requirements(self):
        return {self.timeframe: self.slow + 10}

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(
        self,
        tick: TickData,
        bar_history: Dict[str, List[Bar]],
        current_bars: Dict[str, Bar],
    ) -> WorkerResult:
        """MACD computation mit künstlicher Last"""

        # === KÜNSTLICHE LAST ===
        self._simulate_heavy_computation()

        # === SIMPLE MACD BERECHNUNG ===
        bars = bar_history.get(self.timeframe, [])

        if len(bars) < self.slow:
            return WorkerResult(
                worker_name=self.name,
                value={"macd": 0.0, "signal": 0.0, "histogram": 0.0},
                confidence=0.0,
            )

        prices = np.array([bar.close for bar in bars])

        # EMA calculation (simplified)
        ema_fast = self._ema(prices, self.fast)
        ema_slow = self._ema(prices, self.slow)
        macd_line = ema_fast[-1] - ema_slow[-1]

        # Signal line (simplified)
        signal_value = macd_line * 0.9
        histogram = macd_line - signal_value

        return WorkerResult(
            worker_name=self.name,
            value={
                "macd": float(macd_line),
                "signal": float(signal_value),
                "histogram": float(histogram),
            },
            confidence=1.0,
            metadata={"artificial_load_ms": self.artificial_load_ms},
        )

    def _ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Simple EMA calculation"""
        alpha = 2 / (period + 1)
        ema = np.zeros_like(prices)
        ema[0] = prices[0]
        for i in range(1, len(prices)):
            ema[i] = alpha * prices[i] + (1 - alpha) * ema[i-1]
        return ema

    def _simulate_heavy_computation(self):
        """Simuliert ML-ähnliche Berechnungen"""
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0

        input_size = 100
        hidden_size = 50

        while (time.perf_counter() - start) < target_duration:
            inputs = np.random.rand(input_size)
            weights1 = np.random.rand(input_size, hidden_size)
            hidden = np.tanh(np.dot(inputs, weights1))
            weights2 = np.random.rand(hidden_size, 10)
            output = np.dot(hidden, weights2)
            exp_output = np.exp(output - np.max(output))
            result = exp_output / exp_output.sum()
            _ = result.sum()
