"""
Every gap category is exactly one kind: a data gap, a market closure, or no gap at all.

The kind decides what a check may do with a gap — a start inside an admitted data gap moves to its
end, a start inside a closure is refused — so a category that arrives without a kind would be
treated as neither, silently. The declaration lives on the enum; this holds it complete.
"""
import pytest

from python.framework.types.coverage_report_types import GapCategory


class TestEveryGapCategoryIsOneKind:
    """Each member of GapCategory answers to exactly one kind."""

    @pytest.mark.parametrize('category', list(GapCategory), ids=lambda c: c.value)
    def test_a_category_is_exactly_one_kind(self, category: GapCategory):
        kinds = [category.is_data_gap(), category.is_market_closure(), category is GapCategory.SEAMLESS]
        assert kinds.count(True) == 1, f'{category.value} is {kinds.count(True)} kinds'

    @pytest.mark.parametrize('category', [GapCategory.SHORT, GapCategory.MODERATE, GapCategory.LARGE],
                             ids=lambda c: c.value)
    def test_a_stretch_without_ticks_is_a_data_gap(self, category: GapCategory):
        assert category.is_data_gap()

    @pytest.mark.parametrize('category', [GapCategory.WEEKEND, GapCategory.HOLIDAY], ids=lambda c: c.value)
    def test_a_weekend_or_a_holiday_is_a_market_closure(self, category: GapCategory):
        assert category.is_market_closure()
