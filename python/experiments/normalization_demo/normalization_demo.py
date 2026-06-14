"""Demo: why normalization makes indicator values cross-instrument comparable.

Runs the same "trend strength" scenario on a calm instrument (EURUSD) and a
wild one (BTCUSD). The raw price-space numbers (slope in price units, band
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


def _band_position(price: float, closes: List[float], deviation: float = 2.0) -> tuple:
    """
    Price position within a Bollinger band (raw distance vs %B).

    Args:
        price: Current price
        closes: Close-price window
        deviation: Std multiplier for the band

    Returns:
        (raw_distance_to_lower, percent_b)
    """
    mid = mean(closes)
    std = pstdev(closes)
    lower = mid - deviation * std
    upper = mid + deviation * std
    return price - lower, Normalizer.rescale(price, lower, upper)


def _print_row(label: str, raw: str, normalized: str) -> None:
    print(f"   {label:<34} {raw:>22} {normalized:>16}")


def main() -> None:
    # Calm vs wild: comparable trend strength, vastly different price scale + volatility.
    eurusd = [1.0800, 1.0802, 1.0804, 1.0806, 1.0808, 1.0811, 1.0813]
    btcusd = [60000, 60130, 60260, 60390, 60520, 60680, 60810]

    eur_delta, eur_std, eur_slope = _slope_in_vol_units(eurusd)
    btc_delta, btc_std, btc_slope = _slope_in_vol_units(btcusd)

    eur_dist, eur_pb = _band_position(eurusd[-1] + 0.0006, eurusd)
    btc_dist, btc_pb = _band_position(btcusd[-1] + 360, btcusd)

    print("=" * 76)
    print("📐 NORMALIZATION DEMO — raw price-space vs cross-instrument-comparable")
    print("=" * 76)
    print()
    print("   Same trend scenario, two instruments:")
    print(f"      EURUSD (calm)  closes ~{eurusd[0]:.4f}, std {eur_std:.5f}")
    print(f"      BTCUSD (wild)  closes ~{btcusd[0]:.0f}, std {btc_std:.2f}")
    print()
    print("─" * 76)
    print("   MIDLINE SLOPE")
    print("─" * 76)
    _print_row("", "raw (price/bar)", "normalize()")
    _print_row("EURUSD", f"{eur_delta:.6f}", f"{eur_slope:.3f}")
    _print_row("BTCUSD", f"{btc_delta:.2f}", f"{btc_slope:.3f}")
    print()
    print("   → raw slopes differ by ~6 orders of magnitude (no shared threshold);")
    print("     in volatility units both read as the SAME trend strength.")
    print()
    print("─" * 76)
    print("   BAND POSITION (%B)")
    print("─" * 76)
    _print_row("", "raw dist. to lower band", "rescale() = %B")
    _print_row("EURUSD", f"{eur_dist:.6f}", f"{eur_pb:.3f}")
    _print_row("BTCUSD", f"{btc_dist:.2f}", f"{btc_pb:.3f}")
    print()
    print("   → raw distances incomparable; %B places both on the same 0..1 scale.")
    print("=" * 76)


if __name__ == '__main__':
    main()
