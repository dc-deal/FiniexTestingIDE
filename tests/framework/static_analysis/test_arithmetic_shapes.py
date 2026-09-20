"""
No formula that lives in a single-source module is silently rewritten somewhere else.

The class of bug this exists for: `SpreadFee.calculate_cost` computed
`(ask - bid) * 10**digits * tick_value * lots` while `gross_pnl_from_price_diff` computed
`price_diff * 10**digits * tick_value * lots`. The same conversion of the same quantity, in
two modules, under different names — so the spread was charged once in the fill price and a
second time as a fee, for eleven months, in every MT5 backtest. Nothing could see it: the
identifiers differ, so no text search finds the pair, and two lines sit far below any clone
detector's minimum token count. It was found by putting the two functions side by side, which
is luck, not method.

§38, §45 and §46 each carry a REPORT RULE saying their mathematics must not be reimplemented
elsewhere. Until now those were held by review alone. This is the first machine check on them.

**It is a RATCHET, not a fund generator, and the numbers say so honestly.** On the tree it was
written against it reports five shapes, of which exactly one is a real duplication — the one
already known. Every other is a shape collision between unrelated code, and each is recorded
below with the reason it is allowed. The value is prospective: a NEW formula duplication
becomes visible on the day it lands, instead of in eleven months.

Scope and cost: `python/` and `tests/`, 841 files and 8 MB of source, measured at ~5 s.

Two ways this baseline could rot, and both are closed. A new leak fails the test, so nothing
creeps in unnoticed. And a baseline entry whose leak no longer exists ALSO fails, so a fix has
to prune its own waiver in the same change — otherwise the list grows into a heap that
certifies nothing, the way the fee-breakdown invariant did.
"""

import ast
from pathlib import Path
from typing import Dict, Tuple

import pytest

from tests.framework.static_analysis.arithmetic_shape_scanner import (
    normalize,
    scan,
    single_source_leaks,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCANNED = ('python', 'tests')

# Every shape below occurs BOTH in a single-source module and outside it, and every one has
# been read at its sites. The reason is the entry — a bare shape here would be a waiver
# nobody can re-judge.
ACCEPTED_LEAKS: Dict[str, str] = {
    '(_*(10**_))':
        'THE FOUNDING CASE, and it is on its way out. `pnl_math.gross_pnl_from_price_diff` '
        'against `SpreadFee.calculate_cost`. Since the spread stopped being booked as a fee '
        'the SpreadFee is uncalled, and #244\'s reporting half deletes it together with its '
        'factory. When that lands this entry must go with it.',

    '((_+_)/2.0)':
        '`price_trigger.mid_price` against three sites, NONE of them a defect. '
        '`market_data_types.TickData.mid` is inline on purpose and says so: delegating was '
        'measured at +30 % on an access that sits in the tick loop (§17). '
        '`tick_parquet_reader` computes it over a pandas Series, which is the series form §46 '
        'explicitly keeps beside the scalar one. And `simple_consensus` averages two '
        'CONFIDENCE values — same shape, nothing to do with a midpoint.',

    '(100.0-(100.0/(1.0+_)))':
        'The RSI formula against `experiments/gil_benchmark`, whose own comment reads '
        '"RSI-like calculations (NumPy releases GIL)" and throws the result into `_`. It is '
        'synthetic CPU load shaped like an indicator, not an indicator.',

    '(100.0/(1.0+_))':
        'The inner half of the entry above, reported separately because the scanner keeps '
        'sub-expressions. Same site, same verdict.',

    '((_-_)*_)':
        'The EMA recursion in `macd.py` against '
        '`now - timedelta(seconds=(COUNT - index) * CADENCE)` in the signal mock producer. A '
        'timestamp offset that happens to have the shape of a recursive average.',
}


@pytest.fixture(scope='module')
def leaks() -> Dict[str, Tuple[list, list]]:
    """The scan result over the project, gathered once per module."""
    return single_source_leaks(scan(PROJECT_ROOT, SCANNED))


def _describe(shape: str, sites: Tuple[list, list]) -> str:
    """
    Render one leak for a failure message.

    Args:
        shape: The normalized shape
        sites: Its (single-source files, outside files) pair

    Returns:
        A block naming the shape and every file it was found in
    """
    inside, outside = sites
    lines = [f'  {shape}']
    lines += [f'      single source : {path}' for path in inside]
    lines += [f'      also in       : {path}' for path in outside]
    return '\n'.join(lines)


class TestNoFormulaLeavesItsSingleSource:
    """The ratchet itself."""

    def test_no_new_leak_appeared(self, leaks):
        """
        A formula from `trading_math/` was reimplemented outside it.

        Read the site before adding a waiver: on this project four of five such reports were
        shape collisions between unrelated code, so the question is never "is the shape the
        same" but "is the QUANTITY the same".
        """
        fresh = {shape: sites for shape, sites in leaks.items()
                 if shape not in ACCEPTED_LEAKS}

        assert not fresh, (
            'A formula that lives in a single-source module now also lives outside it:\n\n'
            + '\n\n'.join(_describe(shape, sites) for shape, sites in sorted(fresh.items()))
            + '\n\nEither route the outside site through the single source (§19), or — when '
              'it is a shape collision between unrelated quantities — add it to '
              'ACCEPTED_LEAKS with the reason you read at the site.'
        )

    def test_no_waiver_outlived_its_leak(self, leaks):
        """
        A waiver whose leak is gone is a waiver nobody will re-judge.

        This is what stops the baseline becoming a heap: a fix that removes a duplication has
        to remove its own entry in the same change.
        """
        stale = sorted(set(ACCEPTED_LEAKS) - set(leaks))

        assert not stale, (
            f'ACCEPTED_LEAKS waives shapes that no longer leak: {stale}. '
            f'Remove them — a waiver for a fixed problem hides the next one.'
        )


class TestTheScannerFindsWhatItWasBuiltFor:
    """
    Without this, a threshold change silently blinds the scanner — which nearly happened on
    the day it was written: at a three-operator minimum the founding case disappeared,
    because `_*(10**_)` has exactly two.
    """

    def test_it_finds_the_case_it_was_built_from(self):
        """The two real formulas, verbatim, must reduce to one shape."""
        # Both are quoted as they really stand: each module puts the subtraction on its own
        # line, so what the scanner compares is the CONVERSION, and that is exactly the part
        # that made the two the same quantity.
        fee = ast.parse('spread_raw * (10 ** self.digits)').body[0].value   # trading_fees.py
        pnl = ast.parse('price_diff * (10 ** digits)').body[0].value        # pnl_math.py

        assert normalize(fee) == normalize(pnl) == '(_*(10**_))'

        # And the whole one-line forms still agree from the conversion outwards, which is what
        # a reader comparing the two functions would see.
        whole_fee = ast.parse('(ask - bid) * (10 ** d) * tick_value * lots').body[0].value
        whole_pnl = ast.parse('price_diff * (10 ** d) * tick_value * lots').body[0].value
        assert normalize(whole_fee).endswith('*(10**_))*_)*_)')
        assert normalize(whole_pnl).endswith('*(10**_))*_)*_)')

    def test_identifiers_do_not_matter_but_literals_do(self):
        """
        The whole method in two assertions: names collapse, numbers do not. A scanner that
        erased the literal would match every power against every other one.
        """
        assert normalize(ast.parse('a * (10 ** b)').body[0].value) == \
            normalize(ast.parse('totally_other * (10 ** name)').body[0].value)
        assert normalize(ast.parse('a * (10 ** b)').body[0].value) != \
            normalize(ast.parse('a * (2 ** b)').body[0].value)
