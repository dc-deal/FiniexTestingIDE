"""
Report IO Encoding Tests (#391).

Report artifacts are JSON, and JSON is UTF-8 by RFC 8259 — so neither the writing nor the
reading process may let its own locale decide the codec. A run is written by one process
(the container) and read back by another (an API server the operator may start anywhere);
if either side falls back to a platform default, the text silently changes on the way.

The failure this suite locks out was observed: our warning messages carry '—' and '⚠️', and
a locale read of those bytes as cp1252 turns the first into mojibake and makes the second
raise outright, because UTF-8's third emoji byte (0x8F) is undefined in cp1252.
"""

from pathlib import Path

from python.framework.reporting.io.trade_history_report_io import (
    TRADE_HISTORY_ARTIFACT,
    TRADE_HISTORY_CSV,
    read_trade_history_report,
    write_trade_history_csv,
    write_trade_history_report,
)
from python.framework.reporting.io.warnings_errors_report_io import (
    WARNINGS_ERRORS_ARTIFACT,
    read_warnings_errors_report,
    write_warnings_errors_report,
)
from python.framework.types.api.report_types import (
    TradeHistoryReport,
    WarningRow,
    WarningsErrorsReport,
)

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

# Both characters are real content in shipped artifacts: the em-dash in validator messages,
# the warning sign in the dry_run override warning.
_EM_DASH = 'consumes a source — either add one'
_EMOJI = '⚠️ dry_run OVERRIDE by profile'

# UTF-8 for '⚠️' — the trailing 0x8F is undefined in cp1252, which is what makes a locale
# read fail loudly on some artifacts and corrupt others quietly.
_EMOJI_UTF8 = _EMOJI.encode('utf-8')


def _report() -> WarningsErrorsReport:
    return WarningsErrorsReport(run_id=_RUN_ID, warnings=[
        WarningRow(tier='minor', scope='s', message=_EM_DASH),
        WarningRow(tier='minor', scope='s', message=_EMOJI),
    ])


class TestJsonArtifactEncoding:
    def test_non_ascii_survives_the_round_trip(self, tmp_path):
        """What the builder put in is what the reader gets back, character for character."""
        write_warnings_errors_report(_report(), tmp_path)
        back = read_warnings_errors_report(tmp_path / WARNINGS_ERRORS_ARTIFACT)
        assert [w.message for w in back.warnings] == [_EM_DASH, _EMOJI]

    def test_artifact_is_utf8_on_disk(self, tmp_path):
        """The bytes are UTF-8 regardless of the writing process's locale."""
        path = write_warnings_errors_report(_report(), tmp_path)
        raw = path.read_bytes()
        assert _EMOJI_UTF8 in raw
        raw.decode('utf-8')  # raises if the writer used a platform codec

    def test_a_locale_read_would_have_corrupted_it(self, tmp_path):
        """Documents the defect: the same clean bytes, read with the wrong codec."""
        path = write_warnings_errors_report(_report(), tmp_path)
        mangled = path.read_bytes().decode('cp1252', errors='surrogateescape')
        assert 'â€”' in mangled                    # em-dash, quietly corrupted
        assert '\udc8f' in mangled                 # 0x8F has no cp1252 mapping at all
        assert read_warnings_errors_report(path).warnings[0].message == _EM_DASH


class TestCsvSurfaceEncoding:
    def test_csv_is_utf8_on_disk(self, tmp_path):
        """The CSV surface carries the same text and must not depend on the locale either."""
        report = TradeHistoryReport(run_id=_RUN_ID, trades=[], count=0, symbols=[], analytics=[])
        write_trade_history_report(report, tmp_path)
        path = write_trade_history_csv(report, tmp_path)
        path.read_bytes().decode('utf-8')
        assert read_trade_history_report(tmp_path / TRADE_HISTORY_ARTIFACT) == report
        assert path.name == TRADE_HISTORY_CSV


class TestNoUnitLeavesTheCodecToTheLocale:
    def test_every_io_unit_names_its_encoding(self):
        """A new IO unit must not reintroduce the platform default (drift guard)."""
        offenders = []
        for unit in sorted(Path('python/framework/reporting/io').glob('*.py')):
            for number, line in enumerate(unit.read_text(encoding='utf-8').splitlines(), 1):
                text_io = ('.read_text()' in line
                           or ('write_text(' in line and 'encoding=' not in line)
                           or (".open('w'" in line and 'encoding=' not in line))
                if text_io:
                    offenders.append(f'{unit.name}:{number}')
        assert offenders == []
