"""
The fill slippage's fold table (#631, #557) — one fill per row.

Per reference kind, per fill and unweighted: every fill counts once, whatever its size, so the
mean says how far a typical fill landed from the price its order named.
"""

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import FillSlippageSummary
from python.framework.types.fill_slippage_types import FillSlippage

SLIPPAGE_TABLE = FoldTable(
    name='fill_slippage',
    figures={
        'measured_against': FigureDeclaration(Reduction.FIRST_ROW),
        'fills': FigureDeclaration(Reduction.COUNT),
        # A fill whose reference was never captured is counted, and not measured
        'fills_measured': FigureDeclaration(Reduction.COUNT, source='slippage_pct'),
        'adverse_pct_mean': FigureDeclaration(Reduction.MEAN, source='slippage_pct'),
        'adverse_pct_max': FigureDeclaration(Reduction.MAX, source='slippage_pct'),
    },
    # The family lists the kinds in the order `SlippageReference` declares them
    order=GroupOrder.FIRST_SEEN,
    grain=('measured_against',),
    row_model=FillSlippage,
    served_as=FillSlippageSummary,
)
