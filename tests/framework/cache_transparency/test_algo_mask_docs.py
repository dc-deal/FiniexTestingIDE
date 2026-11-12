"""
The Algo Masks' Documentation — every mask's table names exactly the fields the mask shows (#653).

`docs/user_guides/algo_masks.md` gives each mask a table of its fields and where each value comes
from. The mask's field list is its class, so the table is held to the class here: a field the mask
gains without a row, or a row the mask no longer has, fails the suite. A new mask adds its class to
DOCUMENTED_MASKS and its section to the page.
"""

import re
from pathlib import Path
from typing import List, Type

import pytest

from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.trading_env.algo_masks.algo_order import AlgoOrder
from python.framework.trading_env.algo_masks.algo_order_result import AlgoOrderResult
from python.framework.trading_env.algo_masks.algo_position import AlgoPosition

DOC = Path('docs/user_guides/algo_masks.md')

# Every algo mask and the page documents, each under a heading that opens with its class name
DOCUMENTED_MASKS: List[Type[AbstractAlgoMask]] = [AlgoPosition, AlgoOrder, AlgoOrderResult]


def _documented_fields(mask: Type[AbstractAlgoMask]) -> List[str]:
    """The field names in the first column of the mask's table, in the page's order."""
    text = DOC.read_text(encoding='utf-8')
    heading = re.search(rf'^## `{mask.__name__}`.*$', text, re.MULTILINE)
    assert heading, f'{DOC} has no section headed `{mask.__name__}`'
    section = text[heading.end():]
    next_heading = re.search(r'^## ', section, re.MULTILINE)
    if next_heading:
        section = section[:next_heading.start()]
    return re.findall(r'^\| `([a-z_]+)` \|', section, re.MULTILINE)


@pytest.mark.parametrize('mask', DOCUMENTED_MASKS, ids=lambda mask: mask.__name__)
class TestEveryMaskIsDocumented:
    """The page and the class name the same fields."""

    def test_no_field_without_a_row(self, mask):
        missing = set(mask.get_mask_fields()) - set(_documented_fields(mask))
        assert not missing, f'{mask.__name__} shows {sorted(missing)}, which {DOC} does not list'

    def test_no_row_without_a_field(self, mask):
        stale = set(_documented_fields(mask)) - set(mask.get_mask_fields())
        assert not stale, f'{DOC} lists {sorted(stale)}, which {mask.__name__} does not show'

    def test_each_field_once(self, mask):
        rows = _documented_fields(mask)
        assert len(rows) == len(set(rows)), f'a field of {mask.__name__} is listed twice'
