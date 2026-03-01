"""
Comparison Report
=================
Cross-instrument volatility and liquidity ranking report.
"""

from typing import List, Optional

from python.framework.types.scenario_generator_types import SymbolAnalysis


def print_cross_instrument_ranking(
    analyses: List[SymbolAnalysis],
    current_symbol: str,
    top_count: int = 3
) -> None:
    """
    Print cross-instrument volatility and liquidity ranking.

    Uses linear interpolation between min and max values for percentage scores.
    Highest = 100%, Lowest = 0%.

    Args:
        analyses: List of SymbolAnalysis for all symbols
        current_symbol: The symbol being analyzed (to mark in output)
        top_count: Number of top instruments to display
    """
    if not analyses:
        print("❌ No analysis data available for comparison.")
        return

    # Header
    print("\n" + "═" * 60)
    print("📊 CROSS-INSTRUMENT RANKING")
    print("═" * 60)

    # Volatility ranking
    _print_volatility_ranking(analyses, current_symbol, top_count)

    # Liquidity ranking
    _print_liquidity_ranking(analyses, current_symbol, top_count)

    # Combined score
    _print_combined_ranking(analyses, current_symbol, top_count)

    print("═" * 60 + "\n")


def _print_volatility_ranking(
    analyses: List[SymbolAnalysis],
    current_symbol: str,
    top_count: int
) -> None:
    """
    Print volatility ranking with percentage scale and actual values.

    Args:
        analyses: List of SymbolAnalysis
        current_symbol: Symbol to mark as current
        top_count: Max items to display
    """
    # Sort by ATR% descending
    sorted_analyses = sorted(
        analyses,
        key=lambda a: a.atr_percent,
        reverse=True
    )

    # Get min/max for scaling
    max_val = sorted_analyses[0].atr_percent if sorted_analyses else 1.0
    min_val = sorted_analyses[-1].atr_percent if sorted_analyses else 0.0
    value_range = max_val - min_val if max_val != min_val else 1.0

    print("\n📈 Volatility Ranking (ATR-based):")

    # Build display list
    items_to_show = []

    for i, analysis in enumerate(sorted_analyses):
        rank = i + 1
        pct = (analysis.atr_percent - min_val) / value_range * 100

        # Determine marker
        marker = ""
        if rank == 1:
            marker = " ← Highest"
        elif rank == len(sorted_analyses):
            marker = " ← Lowest"
        if analysis.symbol == current_symbol:
            marker = " ← Current" if not marker else marker.replace(
                "←", "← Current,")

        # Show if in top_count, is current, or is lowest
        show = (
            rank <= top_count or
            analysis.symbol == current_symbol or
            rank == len(sorted_analyses)
        )

        if show:
            # Add ellipsis if gap
            if items_to_show and rank > items_to_show[-1][0] + 1:
                items_to_show.append(
                    (None, None, None, None, None, None, None))

            bar_len = round(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)

            # Format ATR% and pips
            atr_str = f"{analysis.atr_percent:.2f}%"
            if analysis.avg_pips_per_day is not None:
                pips_str = f"{analysis.avg_pips_per_day:.0f}p"
            else:
                pips_str = ""

            items_to_show.append(
                (rank, analysis.symbol, pct, bar, marker, atr_str, pips_str))

    # Print items
    for item in items_to_show:
        if item[0] is None:
            print("   ...")
        else:
            rank, symbol, pct, bar, marker, atr_str, pips_str = item
            if pips_str:
                print(
                    f"   {rank}. {symbol:<8} {pct:5.1f}%  ({atr_str}, {pips_str})  {bar}{marker}")
            else:
                print(
                    f"   {rank}. {symbol:<8} {pct:5.1f}%  ({atr_str})  {bar}{marker}")


def _print_liquidity_ranking(
    analyses: List[SymbolAnalysis],
    current_symbol: str,
    top_count: int
) -> None:
    """
    Print liquidity ranking with percentage scale.

    Args:
        analyses: List of SymbolAnalysis
        current_symbol: Symbol to mark as current
        top_count: Max items to display
    """
    # Calculate ticks per hour for each
    tph_data = [
        (a, _calculate_avg_ticks_per_hour(a)) for a in analyses
    ]

    # Sort by ticks/hour descending
    sorted_data = sorted(tph_data, key=lambda x: x[1], reverse=True)

    # Get min/max for scaling
    max_val = sorted_data[0][1] if sorted_data else 1.0
    min_val = sorted_data[-1][1] if sorted_data else 0.0
    value_range = max_val - min_val if max_val != min_val else 1.0

    print("\n💧 Liquidity Ranking (Ticks/Hour):")

    # Build display list
    items_to_show = []

    for i, (analysis, tph) in enumerate(sorted_data):
        rank = i + 1
        pct = (tph - min_val) / value_range * 100

        # Determine marker
        marker = ""
        if rank == 1:
            marker = " ← Highest"
        elif rank == len(sorted_data):
            marker = " ← Lowest"
        if analysis.symbol == current_symbol:
            marker = " ← Current" if not marker else marker.replace(
                "←", "← Current,")

        # Show if in top_count, is current, or is lowest
        show = (
            rank <= top_count or
            analysis.symbol == current_symbol or
            rank == len(sorted_data)
        )

        if show:
            # Add ellipsis if gap
            if items_to_show and rank > items_to_show[-1][0] + 1:
                items_to_show.append(
                    (None, None, None, None, None))  # Ellipsis marker

            bar_len = round(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            items_to_show.append((rank, analysis.symbol, tph, bar, marker))

    # Print items
    for item in items_to_show:
        if item[0] is None:
            print("   ...")
        else:
            rank, symbol, tph, bar, marker = item
            print(f"   {rank}. {symbol:<8} {tph:>7,.0f}  {bar}{marker}")


def _print_combined_ranking(
    analyses: List[SymbolAnalysis],
    current_symbol: str,
    top_count: int
) -> None:
    """
    Print combined score ranking (volatility × liquidity).

    Args:
        analyses: List of SymbolAnalysis
        current_symbol: Symbol to mark as current
        top_count: Max items to display
    """
    # Get volatility min/max
    vol_values = [a.atr_percent for a in analyses]
    vol_max = max(vol_values) if vol_values else 1.0
    vol_min = min(vol_values) if vol_values else 0.0
    vol_range = vol_max - vol_min if vol_max != vol_min else 1.0

    # Get liquidity min/max
    liq_values = [_calculate_avg_ticks_per_hour(a) for a in analyses]
    liq_max = max(liq_values) if liq_values else 1.0
    liq_min = min(liq_values) if liq_values else 0.0
    liq_range = liq_max - liq_min if liq_max != liq_min else 1.0

    # Calculate combined scores
    scored = []
    for analysis in analyses:
        vol_norm = (analysis.atr_percent - vol_min) / vol_range
        tph = _calculate_avg_ticks_per_hour(analysis)
        liq_norm = (tph - liq_min) / liq_range
        score = vol_norm * liq_norm * 100
        scored.append((analysis, score))

    # Sort by score descending
    sorted_scored = sorted(scored, key=lambda x: x[1], reverse=True)

    # Get min/max for scaling
    max_score = sorted_scored[0][1] if sorted_scored else 1.0
    min_score = sorted_scored[-1][1] if sorted_scored else 0.0
    score_range = max_score - min_score if max_score != min_score else 1.0

    print("\n⚡ Combined Score (Volatility × Liquidity):")

    # Build display list
    items_to_show = []

    for i, (analysis, score) in enumerate(sorted_scored):
        rank = i + 1
        pct = (score - min_score) / score_range * 100

        # Determine marker
        marker = ""
        if rank == 1:
            marker = " ← Highest"
        elif rank == len(sorted_scored):
            marker = " ← Lowest"
        if analysis.symbol == current_symbol:
            marker = " ← Current" if not marker else marker.replace(
                "←", "← Current,")

        # Show if in top_count, is current, or is lowest
        show = (
            rank <= top_count or
            analysis.symbol == current_symbol or
            rank == len(sorted_scored)
        )

        if show:
            # Add ellipsis if gap
            if items_to_show and rank > items_to_show[-1][0] + 1:
                items_to_show.append((None, None, None, None, None))

            bar_len = round(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            items_to_show.append((rank, analysis.symbol, score, bar, marker))

    # Print items
    for item in items_to_show:
        if item[0] is None:
            print("   ...")
        else:
            rank, symbol, score, bar, marker = item
            print(f"   {rank}. {symbol:<8} {score:>6.1f}   {bar}{marker}")


def _print_ranking_items(
    sorted_analyses: List[SymbolAnalysis],
    current_symbol: str,
    top_count: int,
    value_fn,
    pct_fn,
    format_fn
) -> None:
    """
    Print ranking items with smart display limiting.

    Shows: top_count items + current symbol + lowest item.

    Args:
        sorted_analyses: Sorted list of analyses
        current_symbol: Symbol to mark as current
        top_count: Max top items to show
        value_fn: Function to get raw value from analysis
        pct_fn: Function to get percentage from analysis
        format_fn: Function to format value for display
    """
    items_to_show = []

    for i, analysis in enumerate(sorted_analyses):
        rank = i + 1
        pct = pct_fn(analysis)

        # Determine marker
        marker = ""
        if rank == 1:
            marker = " ← Highest"
        elif rank == len(sorted_analyses):
            marker = " ← Lowest"
        if analysis.symbol == current_symbol:
            marker = " ← Current" if not marker else marker.replace(
                "←", "← Current,")

        # Show if in top_count, is current, or is lowest
        show = (
            rank <= top_count or
            analysis.symbol == current_symbol or
            rank == len(sorted_analyses)
        )

        if show:
            # Add ellipsis if gap
            if items_to_show and rank > items_to_show[-1][0] + 1:
                items_to_show.append((None, None, None, None, None))

            bar_len = round(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)
            formatted = format_fn(analysis, pct)
            items_to_show.append(
                (rank, analysis.symbol, formatted, bar, marker))

    # Print items
    for item in items_to_show:
        if item[0] is None:
            print("   ...")
        else:
            rank, symbol, formatted, bar, marker = item
            print(f"   {rank}. {symbol:<8} {formatted}  {bar}{marker}")


def _calculate_avg_ticks_per_hour(analysis: SymbolAnalysis) -> float:
    """
    Calculate average ticks per hour for a symbol.

    Args:
        analysis: SymbolAnalysis data

    Returns:
        Average ticks per hour
    """
    if not analysis.periods:
        return 0.0

    total_ticks = sum(p.tick_count for p in analysis.periods)
    total_hours = len(analysis.periods)  # Each period is 1 hour

    return total_ticks / total_hours if total_hours > 0 else 0.0
