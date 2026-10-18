"""
FiniexTestingIDE - Data Deficiency Types

What an archived file LACKS, said once and checked everywhere. The origin says WHO produced a
file; a deficiency says what a reader of a result is missing because of it. Kraken's published
history has real trades and no quotes; the next external source will lack something else, and a
collected file can lack things too. So the lack is its own closed vocabulary, measured per file,
and every value is declared beside it — held complete against the enum by a test, so a new value
cannot arrive without saying how it is established and what it costs a run.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from python.framework.types.validation_types import Severity


class DataDeficiency(str, Enum):
    """
    What an archived file lacks.

    Deliberately NOT values, so the set sorts something: a property of the venue (a quote-driven
    venue has no traded price — its price formation already says so), a conversion defect (fixed
    at import, never kept as a property), gaps in time (measured and classified elsewhere),
    generated ticks (no data has them, and the rule stays never to generate ticks), storage
    choices and run-time states.
    """

    NO_QUOTE = 'no_quote'
    NO_ARRIVAL_TIME = 'no_arrival_time'
    ARRIVAL_TIME_RECONSTRUCTED = 'arrival_time_reconstructed'
    MISSING_TRADES = 'missing_trades'
    SYNTHETIC = 'synthetic'


@dataclass(frozen=True)
class DeficiencyDeclaration:
    """
    How one deficiency is known, and what it costs a result.

    Args:
        check: The validation check id its finding carries
        title: Its name in a finding, as the console prints it
        established: How a file is found to have it — measured or derived, never inferred from
            a version number
        consequence: What a run that read such data cannot say
        severity: Default severity of the finding
    """
    check: str
    title: str
    established: str
    consequence: str
    severity: Severity


DEFICIENCY_DECLARATIONS: Dict[DataDeficiency, DeficiencyDeclaration] = {
    DataDeficiency.NO_QUOTE: DeficiencyDeclaration(
        check='data_no_quote',
        title='NO QUOTE',
        established='measured: bid equals ask on every row of the file',
        consequence='bid = ask = traded price as recorded, so no spread was measured there',
        severity=Severity.WARNING),
    DataDeficiency.NO_ARRIVAL_TIME: DeficiencyDeclaration(
        check='data_no_arrival_time',
        title='NO ARRIVAL TIME',
        established='measured: the file carries no collected_msc column, or only zeros',
        consequence='no receive time: inter-tick intervals and a tick-processing budget fall '
                    'back to event time',
        severity=Severity.WARNING),
    DataDeficiency.ARRIVAL_TIME_RECONSTRUCTED: DeficiencyDeclaration(
        check='data_arrival_time_reconstructed',
        title='ARRIVAL TIME RECONSTRUCTED',
        established='measured by the fingerprints of the two reconstructions: every '
                    'multi-tick second ending at .999, or the arrival time copied from the '
                    'event time with no receive lag',
        consequence='collected_msc was reconstructed after the fact, not recorded on arrival',
        severity=Severity.WARNING),
    DataDeficiency.MISSING_TRADES: DeficiencyDeclaration(
        check='data_missing_trades',
        title='MISSING TRADES',
        established='measured from the venue\'s trade ids, where every trade carries one; a '
                    'deficiency only above the limits below, a count always',
        consequence='the file misses trades the venue printed, so a backtest reads less than '
                    'the market did',
        severity=Severity.WARNING),
    DataDeficiency.SYNTHETIC: DeficiencyDeclaration(
        check='data_synthetic',
        title='SYNTHETIC',
        established='derived from the producer\'s own data_origin field on the signal envelope',
        consequence='generated data, not a market record: valid for pipeline validation, '
                    'meaningless as a performance statement',
        severity=Severity.WARNING),
}

# When missing trade ids become a deficiency: a file with a real hole, not the reconnect noise
# nearly every collected file shows (0.27–0.43 % of the id span, measured 2026-10-05). More than
# one percent of the span, or one run of missing ids longer than five minutes.
MISSING_TRADES_SHARE_LIMIT = 0.01
MISSING_TRADES_RUN_LIMIT_S = 300.0


@dataclass
class TradeIdGaps:
    """
    The venue's trade ids a file does not hold, measured where every trade carries its id.

    Args:
        span: Ids from the file's first to its last trade, both included
        missing: Ids in that span the file does not hold
        runs: Number of runs of consecutive missing ids
        longest_run: Ids in the longest run
        longest_run_seconds: Event time between the two trades around the longest run
    """
    span: int
    missing: int
    runs: int
    longest_run: int
    longest_run_seconds: float

    def is_a_hole(self) -> bool:
        """
        Whether the missing ids amount to a real hole rather than reconnect noise.

        Returns:
            True above either limit
        """
        share = self.missing / self.span if self.span else 0.0
        return share > MISSING_TRADES_SHARE_LIMIT or self.longest_run_seconds > MISSING_TRADES_RUN_LIMIT_S


@dataclass
class TickDeficiencyMeasurement:
    """
    What one tick file lacks, measured from its rows.

    Args:
        deficiencies: The deficiencies found, in declaration order
        trade_id_gaps: The trade-id measurement; None where the file carries no trade ids —
            "not measurable", which is a different statement from nothing missing
    """
    deficiencies: List[DataDeficiency] = field(default_factory=list)
    trade_id_gaps: Optional[TradeIdGaps] = None
