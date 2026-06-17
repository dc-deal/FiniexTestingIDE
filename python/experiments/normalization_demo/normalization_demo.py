"""Demo: why normalization makes indicator values cross-instrument comparable.

Runs the same "trend strength" scenario across four instruments spanning very
different price scales and volatilities (EURUSD → USDJPY → ETHUSD → BTCUSD, calm
forex to wild crypto). The raw price-space numbers (slope in price units, band
distance) differ by orders of magnitude and cannot share a threshold. Routed
through the Normalizer they collapse onto the same dimensionless scale — the
whole reason the Bollinger and ma_trend workers normalize rather than split per
asset class.

Self-contained (synthetic closes, no data dependency) so it is deterministic
and runnable anywhere.
"""

from statistics import mean, pstdev
from typing import List

from python.framework.utils.trading_math.normalizer import Normalizer


def _slope_in_vol_units(closes: List[float]) -> tuple:
    """
    Per-bar midline move and its volatility-normalized value.

    Args:
        closes: Close-price window (rising = uptrend)

    Returns:
        (raw_delta_per_bar, window_std, normalized_slope)
    """
    delta = mean([closes[i] - closes[i - 1] for i in range(1, len(closes))])
    std = pstdev(closes)
    return delta, std, Normalizer.normalize(delta, std)


def _band_position(closes: List[float], overshoot: float = 1.3, deviation: float = 2.0) -> tuple:
    """
    Band position of a probe price set just past the upper band (raw distance vs %B).

    The probe sits `overshoot` volatility units above the last close, so every
    instrument lands at roughly the same %B (slight overshoot) regardless of scale.

    Args:
        closes: Close-price window
        overshoot: Std multiplier placing the probe above the last close
        deviation: Std multiplier for the band

    Returns:
        (raw_distance_to_lower, percent_b)
    """
    mid = mean(closes)
    std = pstdev(closes)
    lower = mid - deviation * std
    upper = mid + deviation * std
    probe = closes[-1] + overshoot * std
    return probe - lower, Normalizer.rescale(probe, lower, upper)


def _print_row(label: str, raw: str, normalized: str) -> None:
    print(f"   {label:<34} {raw:>22} {normalized:>16}")


def main() -> None:
    # Same gentle uptrend, four very different price scales + volatilities.
    instruments = [
        ('EURUSD', 'calm forex',  [1.0800, 1.0802, 1.0804, 1.0806, 1.0808, 1.0811, 1.0813]),
        ('USDJPY', 'forex, ~150', [149.80, 149.83, 149.86, 149.89, 149.92, 149.96, 149.99]),
        ('ETHUSD', 'mid crypto',  [3000.0, 3007.0, 3014.0, 3021.0, 3028.0, 3036.0, 3043.0]),
        ('BTCUSD', 'wild crypto', [60000.0, 60130.0, 60260.0, 60390.0, 60520.0, 60680.0, 60810.0]),
    ]

    rows = []
    for label, note, closes in instruments:
        delta, std, slope = _slope_in_vol_units(closes)
        dist, pb = _band_position(closes)
        rows.append((label, note, closes[0], delta, std, slope, dist, pb))

    print("=" * 76)
    print("📐 NORMALIZATION DEMO — raw price-space vs cross-instrument-comparable")
    print("=" * 76)
    print()
    print("   Same trend scenario across four scales + volatilities:")
    for label, note, first, _, std, _, _, _ in rows:
        tag = f"{label} ({note})"
        print(f"      {tag:<22} closes ~{first:<9g} std {std:.5g}")
    print()
    print("─" * 76)
    print("   MIDLINE SLOPE")
    print("─" * 76)
    _print_row("", "raw (price/bar)", "normalize()")
    for label, _, _, delta, _, slope, _, _ in rows:
        _print_row(label, f"{delta:.6g}", f"{slope:.3f}")
    print()
    print("   → raw slopes span ~6 orders of magnitude (no shared threshold);")
    print("     in volatility units they all read as the SAME trend strength.")
    print()
    print("─" * 76)
    print("   BAND POSITION (%B)")
    print("─" * 76)
    _print_row("", "raw dist. to lower band", "rescale() = %B")
    for label, _, _, _, _, _, dist, pb in rows:
        _print_row(label, f"{dist:.6g}", f"{pb:.3f}")
    print()
    print("   → raw distances incomparable; %B places them all on the same 0..1 scale.")
    print("=" * 76)


if __name__ == '__main__':
    main()
