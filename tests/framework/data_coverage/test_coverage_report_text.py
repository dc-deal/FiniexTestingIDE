"""
FiniexTestingIDE - Coverage Report Without Bars (#586)

`discoveries_cli.py data-coverage show` ended in a traceback for a symbol with no bars — a
mistyped one, or bars never rendered: the report read a time range that does not exist. The
report says so instead.
"""

from python.framework.discoveries.data_coverage.data_coverage_report import DataCoverageReport


def test_a_symbol_without_bars_says_so_instead_of_failing():
    report = DataCoverageReport(symbol='NOPEUSD', broker_type='kraken_spot')

    text = report.generate_report()

    assert '📊 DATA COVERAGE REPORT: kraken_spot/NOPEUSD' in text
    assert '(no bar data for this symbol)' in text
    assert 'Time Range' not in text
