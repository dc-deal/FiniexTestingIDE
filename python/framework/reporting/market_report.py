"""
Market Report
=============
Single symbol market analysis report generator.
"""

from typing import Dict

from python.framework.types.scenario_generator_types import (
    SymbolAnalysis,
    TradingSession,
    VolatilityRegime,
)
from python.framework.utils.activity_volume_provider import get_activity_provider


def print_analysis_report(analysis: SymbolAnalysis) -> None:
    """
    Print formatted analysis report for a single symbol.

    Args:
        analysis: SymbolAnalysis results
    """
    activity_provider = get_activity_provider()

    # Header
    print("\n" + "=" * 60)
    print(f"📊 MARKET ANALYSIS REPORT: {analysis.symbol}")
    print("=" * 60)

    # Overview
    print(f"Data Range:     {analysis.start_time.strftime('%Y-%m-%d')} → "
          f"{analysis.end_time.strftime('%Y-%m-%d')} ({analysis.total_days} days)")
    print(f"Timeframe:      {analysis.timeframe}")
    print(f"Market Type:    {analysis.market_type}")
    print(f"Data Source:    {analysis.data_source}")

    # Divider
    print("\n" + "─" * 60)
    print("📈 VOLATILITY DISTRIBUTION (ATR-based)")
    print("─" * 60)

    # Total coverage time
    total_periods = len(analysis.periods)
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
        count = analysis.regime_distribution.get(regime, 0)
        pct = analysis.regime_percentages.get(regime, 0)
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
    print(f"\n   ATR Relative: {analysis.atr_min:.5f} - {analysis.atr_max:.5f} "
          f"(avg: {analysis.atr_avg:.5f})")

    # Session statistics with regime distribution
    print("\n" + "─" * 60)
    print("📊 SESSION ACTIVITY")
    print("─" * 60)

    activity_label = activity_provider.get_metric_label(
        analysis.market_type
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
        if session not in analysis.session_summaries:
            continue

        summary = analysis.session_summaries[session]

        # Calculate session duration
        session_hours = summary.period_count * granularity_hours
        session_days = session_hours // 24
        session_rem_hours = session_hours % 24

        print(
            f"\n   {session_names[session]} ({summary.period_count} periods, {session_days}d {session_rem_hours}h):")
        print(f"      Total {activity_label}:    {summary.total_ticks:,}")
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
    print(f"   Total bars:      {analysis.total_bars:,}")
    print(f"   Total {activity_label}:    {analysis.total_ticks:,}")
    print(f"   Real bar ratio:  {analysis.real_bar_ratio * 100:.1f}%")

    # Recommendations
    print("\n" + "─" * 60)
    print("💡 GENERATION RECOMMENDATIONS")
    print("─" * 60)
    print(f"   • Balanced testing: --strategy balanced --count 12")
    print(f"   • Chronological:    --strategy blocks --block-size 6")
    print(f"   • Stress testing:   --strategy stress --count 5")
    print(
        f"\n   Run: python scenario_cli.py generate {analysis.symbol} --help")

    print("=" * 60 + "\n")
