"""
The venue's side of the divergence report (#512) — what the venue itself published and booked,
read through the seam's readers.

It reads only what a comparison asks for: the stretch of each run of missing trade ids, the
decision bar of each day proposed as a capture difference, and the account's ledger over the
deployment's span. A read that gave up is named for that stretch, never answered with nothing — a
stretch read in part would read as one without trades, and a ledger read in part as an account
nobody touched. A venue without the record (a forex broker publishes no trades) is named once.
"""

from datetime import datetime, timedelta
from typing import Optional, Sequence, Tuple

from python.framework.exceptions.connection_errors import ConnectionGaveUpError
from python.framework.reporting.builders.divergence.venue_record_builder import (
    trades_inside_run,
)
from python.framework.types.api.divergence_report_types import DivergenceGap
from python.framework.types.divergence_capture_types import (
    VenueBarTrades,
    VenueLedgerRead,
    VenueRunAnswers,
)
from python.framework.venue_history.abstract_public_trade_reader import (
    AbstractPublicTradeReader,
)
from python.framework.venue_history.abstract_venue_ledger_reader import (
    AbstractVenueLedgerReader,
)

# Read past a run's closing trade by this much, so a trade stamped with its very time is read too
_PAST_THE_EDGE = timedelta(milliseconds=1)


class VenueSideCollector:
    """
    Reads the venue's own record for a comparison.

    Args:
        broker_type: The venue's broker type, for the words a missing reader is named with
        trade_reader: The venue's public trade list; None where the venue has none we read
        venue_ledger_reader: The account's ledger at its venue; None where it has no reader
    """

    def __init__(self, broker_type: str, trade_reader: Optional[AbstractPublicTradeReader],
                 venue_ledger_reader: Optional[AbstractVenueLedgerReader]):
        self._broker_type = broker_type
        self._trade_reader = trade_reader
        self._venue_ledger_reader = venue_ledger_reader

    def answer_runs(self, symbol: str, runs: Sequence[DivergenceGap]) -> VenueRunAnswers:
        """
        How many trades the venue published inside each run of trade ids our archive lacks.

        Args:
            symbol: Our symbol
            runs: The runs, each with its two archive trade ids

        Returns:
            The count per run, and each run that could not be read
        """
        if self._trade_reader is None:
            return VenueRunAnswers(unreadable=[
                f'{self._broker_type}: no trade list of the venue is read — the runs of missing '
                f'trade ids stay unconfirmed'])
        answers = VenueRunAnswers()
        for run in runs:
            try:
                trades = self._trade_reader.read_trades(symbol, run.start, run.end + _PAST_THE_EDGE)
            except ConnectionGaveUpError as error:
                answers.unreadable.append(f'trade ids {run.trade_id_before}–{run.trade_id_after} '
                                          f'({run.start.isoformat()}): {error}')
                continue
            answers.counts[(run.trade_id_before, run.trade_id_after)] = trades_inside_run(run,
                                                                                          trades)
        return answers

    def bar_trades(self, symbol: str,
                   bars: Sequence[Tuple[datetime, datetime]]) -> VenueBarTrades:
        """
        The venue's trades of each decision bar.

        Args:
            symbol: Our symbol
            bars: (opening, close) of each bar

        Returns:
            The trades by the bar's opening, and each bar that could not be read
        """
        if self._trade_reader is None:
            return VenueBarTrades(unreadable=[
                f'{self._broker_type}: no trade list of the venue is read — the capture '
                f'differences stay unconfirmed'])
        found = VenueBarTrades()
        for opening, close in bars:
            try:
                found.trades[opening] = self._trade_reader.read_trades(symbol, opening, close)
            except ConnectionGaveUpError as error:
                found.unreadable.append(f'decision bar {opening.isoformat()}: {error}')
        return found

    def read_venue_ledger(self, start: datetime, end: datetime) -> VenueLedgerRead:
        """
        The account's ledger over a stretch.

        Args:
            start: The stretch's start
            end: Its end

        Returns:
            The entries; or why they were not read — the state frames then judge a restart
        """
        if self._venue_ledger_reader is None:
            return VenueLedgerRead(unreadable=f"{self._broker_type}: the account's ledger has "
                                              f'no reader — the state frames judge a restart')
        try:
            return VenueLedgerRead(entries=self._venue_ledger_reader.read_ledger(start, end))
        except ConnectionGaveUpError as error:
            return VenueLedgerRead(unreadable=f"the account's ledger: {error}")
