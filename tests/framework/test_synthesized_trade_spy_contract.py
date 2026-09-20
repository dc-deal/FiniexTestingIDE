"""
Three test spies mirror `_synthesize_pending_trade`'s signature — so a rename fails HERE, once.

Those spies name every parameter on purpose. A spy that swallows its arguments in `**kwargs`
accepts anything and therefore stops being able to report that the real signature moved, which
is a check worth keeping (the reasoning is written at
`tests/simulation/trade_emission/test_trade_emission.py`, second spy).

What it cost without this test is not the duplication but the SHAPE of the failure. Renaming
one parameter on 2026-09-20 produced a `TypeError` at runtime — invisible to pyflakes, invisible
when reading — in three files spread across two suites, discovered one at a time because the
runner stops at the first red suite. Three full-suite runs at roughly fourteen minutes each,
for one rename.

So the spies keep their explicit signatures, and this pins the contract they copy. A rename now
fails one test in seconds, and the message says which files to carry forward.
"""

import inspect

from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor

# The spies live here. Each one names the parameters below and forwards them positionally.
SPY_SITES = (
    'tests/simulation/trade_emission/test_trade_emission.py',
    'tests/parity/test_trade_records_parity.py',
)

# What those spies declare, in order. `position` is optional and part of the contract since
# #503: a close order can name a position other than itself, so the caller resolves it.
EXPECTED_PARAMETERS = (
    'pending_order',
    'fill_price',
    'filled_lots',
    'is_maker',
    'symbol_spec',
    'fee_cost',
    'position',
)


def test_the_spies_still_describe_the_real_signature():
    """
    The parameters of `_synthesize_pending_trade`, pinned by name and order.

    On a mismatch: update EXPECTED_PARAMETERS and every spy in SPY_SITES in the same change.
    """
    parameters = tuple(
        name for name in
        inspect.signature(AbstractTradeExecutor._synthesize_pending_trade).parameters
        if name != 'self'
    )

    assert parameters == EXPECTED_PARAMETERS, (
        f'`_synthesize_pending_trade` now takes {parameters}, pinned here as '
        f'{EXPECTED_PARAMETERS}. The test spies copy this signature by name and will fail at '
        f'RUNTIME, one suite at a time, until they are carried forward too:\n  '
        + '\n  '.join(SPY_SITES)
        + '\nUpdate them and this list in the same change.'
    )


def test_is_maker_is_a_decision_and_not_an_order_type():
    """
    The parameter used to be `entry_type`, and that WAS the defect (#244).

    Deriving liquidity from the order type booked a limit that crossed the book on arrival at
    the maker rate — half the real charge. Worse, the test was made twice: once for the fee and
    once for the synthetic BrokerTrade that feeds the report's liquidity column, so the two
    could disagree. The caller now decides once, from `FillType`, and hands the answer down.
    """
    annotation = inspect.signature(
        AbstractTradeExecutor._synthesize_pending_trade).parameters['is_maker'].annotation

    assert annotation is bool, (
        f'`is_maker` is annotated {annotation!r}. It must stay a plain decision taken by the '
        f'caller — the moment this helper receives an order type again it will re-derive '
        f'liquidity itself, which is exactly how the fee and the report row came to disagree.'
    )
