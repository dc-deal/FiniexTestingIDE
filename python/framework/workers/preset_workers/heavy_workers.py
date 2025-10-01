"""
FiniexTestingIDE - Heavy Workers für Parallelisierungs-Tests
Workers mit künstlicher CPU-Last zum Testen von Parallel-Performance
"""

import time
from typing import Dict, List

import numpy as np

from python.framework.types import Bar, TickData, WorkerContract, WorkerResult
from python.framework.workers.abstract.abstract_blackbox_worker import \
    AbstractBlackboxWorker


class HeavyRSIWorker(AbstractBlackboxWorker):
    """
    RSI Worker mit künstlicher CPU-Last
    Simuliert komplexe Berechnungen (z.B. ML-Model, FFT, etc.)
    """

    def __init__(self, period: int = 14, timeframe: str = "M5",
                 artificial_load_ms: float = 5.0, **kwargs):
        """
        Args:
            period: RSI period
            timeframe: Timeframe to use
            artificial_load_ms: Künstliche CPU-Last in Millisekunden
        """
        self.period = period
        self.timeframe = timeframe
        self.artificial_load_ms = artificial_load_ms
        super().__init__("HeavyRSI", kwargs)

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
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
        """
        RSI computation mit künstlicher CPU-Last
        """

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
        """
        Simuliert CPU-intensive Berechnungen

        Verwendet verschiedene Strategien für realistische CPU-Last:
        1. Matrix-Multiplikationen (simuliert ML-Inference)
        2. Trigonometrische Berechnungen (simuliert FFT)
        3. Pseudo-random number generation (simuliert Monte-Carlo)
        """
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0  # Convert to seconds

        # Strategy 1: Matrix multiplications (CPU-bound)
        size = 50  # 50x50 matrix
        while (time.perf_counter() - start) < target_duration:
            matrix_a = np.random.rand(size, size)
            matrix_b = np.random.rand(size, size)
            result = np.dot(matrix_a, matrix_b)

            # Add some trigonometric operations
            result = np.sin(result) + np.cos(result)

            # Prevent compiler optimization
            _ = result.sum()


class HeavyEnvelopeWorker(AbstractBlackboxWorker):
    """
    Envelope Worker mit künstlicher CPU-Last
    Simuliert komplexe Band-Berechnungen
    """

    def __init__(
        self,
        period: int = 20,
        deviation: float = 0.02,
        timeframe: str = "M5",
        artificial_load_ms: float = 8.0,
        **kwargs
    ):
        self.period = period
        self.deviation = deviation
        self.timeframe = timeframe
        self.artificial_load_ms = artificial_load_ms
        super().__init__("HeavyEnvelope", kwargs)

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
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
        """
        Envelope computation mit künstlicher CPU-Last
        """

        # === KÜNSTLICHE LAST (anders als RSI für Varietät) ===
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
        """
        Simuliert CPU-intensive Berechnungen (andere Methode als RSI)

        Verwendet Fourier-Transform-ähnliche Operationen
        """
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0

        # Strategy 2: FFT-like computations
        size = 1000
        while (time.perf_counter() - start) < target_duration:
            data = np.random.rand(size)

            # Simulate FFT
            fft_result = np.fft.fft(data)
            inverse = np.fft.ifft(fft_result)

            # Add convolution
            kernel = np.random.rand(10)
            conv_result = np.convolve(data[:100], kernel, mode='same')

            # Prevent compiler optimization
            _ = conv_result.sum() + inverse.real.sum()


class HeavyMACDWorker(AbstractBlackboxWorker):
    """
    MACD Worker mit künstlicher CPU-Last
    Zusätzlicher Worker für bessere Parallelisierungs-Tests
    """

    def __init__(
        self,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        timeframe: str = "M5",
        artificial_load_ms: float = 6.0,
        **kwargs
    ):
        self.fast = fast
        self.slow = slow
        self.signal = signal
        self.timeframe = timeframe
        self.artificial_load_ms = artificial_load_ms
        super().__init__("HeavyMACD", kwargs)

    def get_contract(self) -> WorkerContract:
        return WorkerContract(
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
        signal_value = macd_line * 0.9  # Simplified
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
        """Simuliert heavy computation (ML-ähnlich)"""
        start = time.perf_counter()
        target_duration = self.artificial_load_ms / 1000.0

        # Strategy 3: Pseudo-ML inference
        input_size = 100
        hidden_size = 50

        while (time.perf_counter() - start) < target_duration:
            # Simulate neural network layers
            inputs = np.random.rand(input_size)
            weights1 = np.random.rand(input_size, hidden_size)
            hidden = np.tanh(np.dot(inputs, weights1))

            weights2 = np.random.rand(hidden_size, 10)
            output = np.dot(hidden, weights2)

            # Softmax
            exp_output = np.exp(output - np.max(output))
            result = exp_output / exp_output.sum()

            # Prevent optimization
            _ = result.sum()
