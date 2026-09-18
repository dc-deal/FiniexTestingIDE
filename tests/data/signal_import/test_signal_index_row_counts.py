"""
Signal index row-count semantics.

An index entry is one (data_sentiment_type, symbol, file) triple, so `row_count` on it has to
mean that symbol's own rows. It used to carry the whole FILE's count on every one of them,
which is invisible per row and wrong the moment anything sums the column — a file with eight
symbols reported eight times its size, and the error travelled into a coverage figure sent to
a peer before it was caught.

The regression guard is the SUM rather than a single value: one row alone looks plausible
either way, and only adding them up separates the two readings.
"""

import pandas as pd

from python.framework.types.signal_data_types import SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn


def _index_frame(imported_signals) -> pd.DataFrame:
    """
    Read the persisted index table.

    Args:
        imported_signals: The module fixture's prepared tree

    Returns:
        The index as a flat DataFrame
    """
    return pd.read_parquet(imported_signals['processed'] / 'signals_index.parquet')


class TestRowCountIsPerSymbol:

    def test_a_row_count_matches_that_symbols_own_rows(self, imported_signals):
        index = _index_frame(imported_signals)
        parquet = pd.read_parquet(imported_signals['parquet'])
        actual = parquet[SignalParquetColumn.SYMBOL.value].value_counts()

        assert len(index) > 0
        for _, row in index.iterrows():
            assert row['row_count'] == actual[row['symbol']], (
                f"index says {row['row_count']} rows for {row['symbol']}, "
                f'the parquet holds {actual[row["symbol"]]}'
            )

    def test_the_symbol_rows_never_sum_past_the_file(self, imported_signals):
        # The actual regression: a file-wide count repeated per symbol multiplies by the
        # symbol count, so the sum exceeds the file. Summing is what makes it visible.
        index = _index_frame(imported_signals)
        parquet = pd.read_parquet(imported_signals['parquet'])

        assert index['row_count'].sum() <= len(parquet)

    def test_the_envelope_sentinel_is_not_counted_into_a_symbol(self, imported_signals):
        # The sentinel carries the market-wide row and belongs to no symbol, so the symbol
        # counts plus the sentinel's own rows are exactly the file.
        index = _index_frame(imported_signals)
        parquet = pd.read_parquet(imported_signals['parquet'])
        sentinel = int(
            (parquet[SignalParquetColumn.SYMBOL.value] == SIGNAL_ENVELOPE_SYMBOL).sum())

        assert index['row_count'].sum() + sentinel == len(parquet)


class TestCoverageReportsTheSameNumber:

    def test_total_rows_is_the_symbols_own_rows(self, imported_signals):
        # get_symbol_file_coverage sums row_count across a symbol's files, so it inherited
        # the same error one layer up.
        index_manager = imported_signals['index']
        parquet = pd.read_parquet(imported_signals['parquet'])
        actual = parquet[SignalParquetColumn.SYMBOL.value].value_counts()

        for sentiment_type in index_manager.list_sentiment_types():
            for symbol in index_manager.list_symbols(sentiment_type):
                coverage = index_manager.get_symbol_file_coverage(sentiment_type, symbol)
                assert coverage['total_rows'] == actual[symbol]
