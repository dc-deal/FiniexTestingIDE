"""
Gap Listing - Which Gaps a Coverage Report Prints

A report over years of data cannot print every gap: a venue's published history carries tens of
thousands of them. It prints the most recent gaps of each category and counts the rest — per
category, so a burst of recent short gaps never pushes out the one large gap, and newest first,
because the oldest gaps are the least useful to a reader looking for what needs attention. Both
coverage reports, the tick one and the signal one, list by this rule.
"""

import heapq
from collections import Counter
from typing import Callable, List

from python.framework.types.coverage_report_types import Gap, GapCategory
from python.framework.utils.time_utils import ensure_utc_aware

# How many gaps of one category a report lists — the newest ones
GAPS_LISTED_PER_CATEGORY = 10

# The order a report lists the categories that mean data is MISSING in. Which categories those
# are is the category's own answer (`GapCategory.is_data_gap()`); a test holds this order to it.
# Weekend and holiday closures are a market's expected silence and are summarised separately.
MISSING_DATA_CATEGORIES = (GapCategory.LARGE, GapCategory.MODERATE, GapCategory.SHORT)

# The icon each listed category's heading carries
CATEGORY_ICONS = {
    GapCategory.LARGE: '🔴',
    GapCategory.MODERATE: '⚠️ ',
    GapCategory.SHORT: 'ℹ️ ',
}


def newest_gaps(gaps: List[Gap], category: GapCategory,
                limit: int = GAPS_LISTED_PER_CATEGORY) -> List[Gap]:
    """
    The most recent gaps of one category, newest first.

    Args:
        gaps: Every gap of the report
        category: The category to list
        limit: How many to return at most

    Returns:
        Up to `limit` gaps of that category, ordered by start time, newest first
    """
    of_category = (gap for gap in gaps
                   if gap.category == category and gap.gap_start is not None)
    return heapq.nlargest(limit, of_category, key=lambda gap: gap.gap_start)


def listing_heading(icon: str, category: GapCategory, listed: int, total: int) -> str:
    """
    The heading of one category's listing, saying how much of it is shown.

    Args:
        icon: The category's icon
        category: The category listed
        listed: How many gaps the listing shows
        total: How many gaps of the category the report holds

    Returns:
        A heading such as '🔴 LARGE — the last 10 of 1,034', or '🔴 LARGE — all 3'
    """
    if listed >= total:
        return f'{icon} {category.value.upper()} — all {total:,}'
    return f'{icon} {category.value.upper()} — the last {listed:,} of {total:,}'


def recent_gap_listing(gaps: List[Gap], render_line: Callable[[Gap], str]) -> List[str]:
    """
    The listing section of a coverage report: per category, its heading and its newest gaps.

    Args:
        gaps: Every gap of the report
        render_line: Renders one gap as one report line — each report says what it knows
            about a gap, the rule of what is listed stays here

    Returns:
        The section's lines; empty when no gap means missing data
    """
    missing = [gap for gap in gaps if gap.category.is_data_gap()]
    counts = Counter(gap.category for gap in missing)
    lines = []
    for category in MISSING_DATA_CATEGORIES:
        listed = newest_gaps(missing, category)
        if not listed:
            continue
        lines.append(listing_heading(
            CATEGORY_ICONS[category], category, len(listed), counts[category]))
        lines.extend(render_line(gap) for gap in listed)
    return lines


def gap_span(gap: Gap) -> str:
    """
    A gap's start and end, the end shortened to what differs from the start.

    Args:
        gap: The gap to describe

    Returns:
        Text such as '2026-09-20 19:48 → 09-21 08:39', padded to one width
    """
    start = ensure_utc_aware(gap.gap_start)
    end = ensure_utc_aware(gap.gap_end)
    if end.date() == start.date():
        end_text = end.strftime('%H:%M')
    elif end.year == start.year:
        end_text = end.strftime('%m-%d %H:%M')
    else:
        end_text = end.strftime('%Y-%m-%d %H:%M')
    return f"{start.strftime('%Y-%m-%d %H:%M')} → {end_text:<16}"
