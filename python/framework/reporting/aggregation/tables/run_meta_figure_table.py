"""
The run meta's fold table (#557) — one scenario's details per row.

The ticks the algo path processed, summed over the scenarios: the one figure of the run's throughput
that adds up over rows. The rest of the throughput divides that, or the loop's ticks, by a time the
run measured, and is a formula in its builder.
"""

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    Reduction,
)
from python.framework.types.api.report_types import RunMetaReport, ScenarioDetailsRow

RUN_THROUGHPUT_TABLE = FoldTable(
    name='run_throughput',
    figures={
        'total_ticks_processed': FigureDeclaration(Reduction.SUM, source='ticks_processed',
                                                   empty_value=0),
    },
    row_model=ScenarioDetailsRow,
    served_as=RunMetaReport,
    serves_whole=False,
)
