"""
Market Report
=============
Single symbol volatility profile report generator.
"""

from typing import Dict

from python.framework.types.market_types.market_config_types import MarketType
from python.framework.types.market_types.market_volatility_profile_types import (
    SymbolVolatilityProfile,
    TradingSession,
    VolatilityRegime,
)
from python.framework.utils.activity_volume_provider import get_activity_provider


def print_volatility_profile(profile: SymbolVolatilityProfile) -> None:
    """
    Print formatted volatility profile report for a single symbol.

    Args:
        profile: SymbolVolatilityProfile results
    """
    activity_provider = get_activity_provider()

    # Header
    print("\n" + "=" * 60)
    print(f"📊 VOLATILITY PROFILE: {profile.symbol}")
    print("=" * 60)

    # Overview
    print(f"Data Range:     {profile.start_time.strftime('%Y-%m-%d')} → "
          f"{profile.end_time.strftime('%Y-%m-%d')} ({profile.total_days} days)")
    print(f"Timeframe:      {profile.timeframe}")
    print(f"Market Type:    {profile.market_type.value}")
    print(f"Data Source:    {profile.data_source}")

    # Divider
    print("\n" + "─" * 60)
    print("📈 VOLATILITY DISTRIBUTION (ATR-based)")
    print("─" * 60)

    # Total coverage time
    total_periods = len(profile.periods)
    granularity_hours = 1  # From config - regime_granularity_hours
    total_hours = total_periods * granularity_hours
    total_days = total_hours // 24
    remaining_hours = total_hours % 24
    print(
        f"Total Coverage: {total_days}d {remaining_hours}h ({total_periods} periods)\n")

    # Volatility regimes with duration - using actual ratio thresholds
    regime_names = {
        VolatilityRegime.VERY_LOW: "Very Low       (<0.50)",
        VolatilityRegime.LOW: "Low        (0.50-0.80)",
        VolatilityRegime.MEDIUM: "Medium     (0.80-1.20)",
        VolatilityRegime.HIGH: "High       (1.20-1.80)",
        VolatilityRegime.VERY_HIGH: "Very High      (>1.80)",
    }

    for regime in VolatilityRegime:
        count = profile.regime_distribution.get(regime, 0)
        pct = profile.regime_percentages.get(regime, 0)
        bar_len = round(pct / 10)
        bar = "█" * bar_len + "░" * (10 - bar_len)

        # Calculate duration for this regime
        regime_hours = count * granularity_hours
        regime_days = regime_hours // 24
        regime_rem_hours = regime_hours % 24
        duration_str = f"{regime_days:2d}d {regime_rem_hours:2d}h"

        print(
            f"   {regime_names[regime]}:  {count:4d} periods  {bar}  {pct:5.1f}%  → {duration_str}")

    # ATR stats
    print(f"\n   ATR Relative: {profile.atr_min:.5f} - {profile.atr_max:.5f} "
          f"(avg: {profile.atr_avg:.5f})")

    # Session statistics with regime distribution
    print("\n" + "─" * 60)
    print("📊 SESSION ACTIVITY")
    print("─" * 60)

    activity_label = activity_provider.get_metric_label(
        profile.market_type
    ).lower()

    session_names = {
        TradingSession.SYDNEY_TOKYO: "Asian (Sydney/Tokyo)",
        TradingSession.LONDON: "London",
        TradingSession.NEW_YORK: "New York",
        TradingSession.TRANSITION: "Transition",
    }

    # Short regime labels for compact display
    regime_short = {
        VolatilityRegime.VERY_LOW: "VL",
        VolatilityRegime.LOW: "L",
        VolatilityRegime.MEDIUM: "M",
        VolatilityRegime.HIGH: "H",
        VolatilityRegime.VERY_HIGH: "VH",
    }

    for session in TradingSession:
        if session not in profile.session_summaries:
            continue

        summary = profile.session_summaries[session]

        # Calculate session duration
        session_hours = summary.period_count * granularity_hours
        session_days = session_hours // 24
        session_rem_hours = session_hours % 24

        print(
            f"\n   {session_names[session]} ({summary.period_count} periods, {session_days}d {session_rem_hours}h):")
        print(f"      Total ticks:    {summary.total_ticks:,}")

        # Show volume for crypto markets
        if profile.market_type == MarketType.CRYPTO:
            print(f"      Total volume:   {summary.total_activity:,.2f}")
        # For forex: total_activity equals total_ticks, no duplication needed
        print(
            f"      Avg density:    {summary.avg_tick_density:,.0f} {activity_label}/hour")
        print(
            f"      ATR Relative:      {summary.min_atr:.5f} - {summary.max_atr:.5f}")

        # Regime distribution for this session
        if summary.period_count > 0:
            regime_parts = []
            for regime in VolatilityRegime:
                regime_count = summary.regime_distribution.get(regime, 0)
                regime_pct = (regime_count / summary.period_count) * 100
                regime_parts.append(
                    f"{regime_short[regime]}: {regime_pct:.0f}%")
            print(f"      Regimes:        {' | '.join(regime_parts)}")

    # Data quality
    print("\n" + "─" * 60)
    print("📦 DATA QUALITY")
    print("─" * 60)
    print(f"   Total bars:      {profile.total_bars:,}")
    print(f"   Total {activity_label}:    {profile.total_ticks:,}")
    print(f"   Real bar ratio:  {profile.real_bar_ratio * 100:.1f}%")

    # Show total volume for crypto
    if profile.market_type == MarketType.CRYPTO:
        print(f"   Total volume:    {profile.total_activity:,.2f}")
    # Recommendations
    print("\n" + "─" * 60)
    print("💡 GENERATION RECOMMENDATIONS")
    print("─" * 60)
    print(f"   • Chronological:    --block-size 6")
    print(
        f"\n   Run: python python/cli/generator_cli.py generate {profile.symbol} --help")

    print("=" * 60 + "\n")
