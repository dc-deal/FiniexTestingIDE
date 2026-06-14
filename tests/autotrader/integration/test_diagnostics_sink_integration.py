"""
FiniexTestingIDE - Diagnostics CSV Sink Integration (#376, AutoTrader)

End-to-end proof that the AutoTrader pipeline flushes an algo-declared diagnostics
sink to the run directory at session end. Uses a test-only probe decision logic
(tests/fixtures/diagnostics/) that writes one row, run through the mock pipeline.
"""

import csv
import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain


_PROFILE = 'configs/autotrader_profiles/backtesting/diagnostics_probe_test.json'


@pytest.fixture(scope='module')
def probe_session():
    config = load_autotrader_config(_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield trader, result
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)


class TestDiagnosticsSinkAutoTrader:
    """The diagnostics CSV lands in the run dir's diagnostics/ subfolder."""

    def test_diagnostics_csv_written(self, probe_session):
        trader, _ = probe_session
        out = trader._run_dir / 'diagnostics' / 'probe_funnel.csv'
        assert out.exists(), 'probe_funnel.csv not flushed to diagnostics/ subfolder'

    def test_diagnostics_csv_content(self, probe_session):
        trader, _ = probe_session
        out = trader._run_dir / 'diagnostics' / 'probe_funnel.csv'
        rows = list(csv.reader(open(out, newline='')))
        assert rows[0] == ['tick_time', 'note']
        assert len(rows) == 2  # header + exactly one probe row
        assert rows[1][1] == 'probe'

    def test_no_suffix_in_autotrader_filename(self, probe_session):
        """AutoTrader is a single session → bare <name>.csv (no scenario suffix)."""
        trader, _ = probe_session
        assert not list((trader._run_dir / 'diagnostics').glob('probe_funnel_*.csv'))
