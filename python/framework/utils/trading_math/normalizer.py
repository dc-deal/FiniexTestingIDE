"""
Normalizer
==========
Central normalization apparatus: express a price-space quantity as a
dimensionless, cross-instrument-comparable ratio via a local reference
(band width / volatility). One audited path for every worker/indicator that
needs %B-style rescaling, clamping, or volatility-unit scaling.

Why central: a raw indicator value (a price distance, a slope in price units)
is not comparable across instruments — 0.1%/bar is a storm for EURUSD and a calm
day for BTCUSD. Dividing a price-space delta by a local volatility measure yields
a value that means the same thing on any instrument and at any price level. The
established platforms normalize rather than split indicators per asset class
(sklearn StandardScaler/MinMaxScaler, Bollinger %B/BandWidth, CCI, ATR-scaling).
"""


class Normalizer:
    """Stateless normalization helpers (cross-instrument comparable ratios)."""

    @staticmethod
    def rescale(value: float, lower: float, upper: float) -> float:
        """
        Position of a value within a [lower, upper] range (MinMax / %B).

        Unclamped — values below lower return < 0, above upper return > 1, so
        overshoot information is preserved. A degenerate range (upper <= lower)
        returns 0.5 (the neutral midpoint).

        Args:
            value: The value to place within the range
            lower: Range lower bound
            upper: Range upper bound

        Returns:
            (value - lower) / (upper - lower), or 0.5 if upper <= lower
        """
        if upper <= lower:
            return 0.5
        return (value - lower) / (upper - lower)

    @staticmethod
    def clamp(x: float, low: float = 0.0, high: float = 1.0) -> float:
        """
        Clamp a value into [low, high].

        Args:
            x: Value to clamp
            low: Lower bound (default 0.0)
            high: Upper bound (default 1.0)

        Returns:
            x bounded to [low, high]
        """
        return max(low, min(high, x))

    @staticmethod
    def normalize(value: float, scale: float) -> float:
        """
        Express a value in units of a reference scale (volatility scaling).

        The core normalization: a price-space quantity (a slope, a band width)
        divided by a local volatility reference (std, band width, ATR) becomes a
        dimensionless, cross-instrument-comparable ratio. Safe by construction —
        a non-positive scale returns 0.0 (flat / undefined volatility).

        Args:
            value: The price-space quantity to normalize
            scale: The volatility / reference magnitude to divide by

        Returns:
            value / scale, or 0.0 if scale <= 0
        """
        if scale <= 0:
            return 0.0
        return value / scale
