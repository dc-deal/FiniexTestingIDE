"""
Raw Archive Re-import, verify and clean: whether the import rewrote what was extracted, and which
loose copies may then be removed.

Count and names alone cannot see a file the importer refused: it keeps its old parquet and its
index row, so both still match. Inbox and rewritten are the two checks that can. Clean runs only
behind a green verify, deletes only a copy that is SHA-256-equal to its archived member, and
never a file that no zip holds.
"""

from datetime import timedelta

from python.experiments.raw_archive_reimport.raw_archive_reimport_types import (
    ReimportCheckId,
    ReimportScope,
)
from tests.data.raw_archive_reimport.conftest import (
    WINTER_END,
    WINTER_MT5,
    WINTER_START,
    build_standard_archive,
    member_json,
)

WINTER_SCOPE = ReimportScope('mt5', window_start=WINTER_START, window_end=WINTER_END)


def _extract_winter(tree) -> None:
    """
    Build the standard archive and extract its winter mt5 files.

    Args:
        tree: The archive tree
    """
    build_standard_archive(tree)
    assert tree.build_tool().extract(WINTER_SCOPE) == 0


def _checks_by_id(tree) -> dict:
    """
    Run the checks against the current index, as a fresh verify would.

    Args:
        tree: The archive tree

    Returns:
        Check id -> check
    """
    tool = tree.build_tool()
    return {check.check_id: check for check in tool.run_checks(tool.read_manifest())}


class TestVerify:
    """No single check proves the re-import; all of them together do."""

    def test_passes_after_the_import_and_reports_the_shift(self, tree, capsys):
        """Every file rewritten one hour later: all green, the summary says so."""
        _extract_winter(tree)
        tree.simulate_import(shift=timedelta(hours=1))
        tool = tree.build_tool()

        checks = tool.run_checks(tool.read_manifest())
        assert [check.check_id for check in checks] == [
            ReimportCheckId.COUNT, ReimportCheckId.NAMES, ReimportCheckId.INBOX,
            ReimportCheckId.REWRITTEN]
        assert all(check.passed for check in checks)

        assert tool.verify() == 0
        out = capsys.readouterr().out
        assert '3 files shifted by +1 h · 0 unchanged · 0 tick counts changed' in out
        assert ('EURUSD_20260115_040207_ticks.json  start 2026-01-15 01:02:07 → '
                '2026-01-15 02:02:07') in out

    def test_count_and_names_cover_the_broker_and_symbols_not_the_window(self, tree):
        """Three winter files were selected; the count compares all five mt5 files."""
        _extract_winter(tree)
        tree.simulate_import()

        assert _checks_by_id(tree)[ReimportCheckId.COUNT].detail == 'index 5 = archive 5'

    def test_a_refused_file_fails_inbox_and_rewritten_while_count_and_names_pass(self, tree):
        """The case the count and the names cannot see."""
        _extract_winter(tree)
        refused = 'GBPUSD_20260120_000000_ticks.json'
        tree.simulate_import(shift=timedelta(hours=1), refused=[refused])

        checks = _checks_by_id(tree)
        assert checks[ReimportCheckId.COUNT].passed
        assert checks[ReimportCheckId.NAMES].passed
        assert not checks[ReimportCheckId.INBOX].passed
        assert checks[ReimportCheckId.INBOX].offenders == (refused,)
        assert not checks[ReimportCheckId.REWRITTEN].passed
        [offender] = checks[ReimportCheckId.REWRITTEN].offenders
        assert offender.startswith(f'{refused} — ') and 'before its extraction' in offender
        assert tree.build_tool().verify() == 1

    def test_an_import_without_override_leaves_every_file_in_the_inbox(self, tree):
        """Without --override the importer skips every file as a duplicate."""
        _extract_winter(tree)
        tree.simulate_import(refused=WINTER_MT5)

        checks = _checks_by_id(tree)
        assert sorted(checks[ReimportCheckId.INBOX].offenders) == sorted(WINTER_MT5)
        assert checks[ReimportCheckId.INBOX].detail.startswith('3 of 3 left in ')

    def test_a_stale_parquet_fails_rewritten_while_count_names_and_inbox_pass(self, tree):
        """The file left the inbox, but its parquet is older than its extraction."""
        _extract_winter(tree)
        stale = 'USDJPY_20260110_220000_ticks.json'
        tree.simulate_import(not_rewritten=[stale])

        checks = _checks_by_id(tree)
        assert checks[ReimportCheckId.COUNT].passed
        assert checks[ReimportCheckId.NAMES].passed
        assert checks[ReimportCheckId.INBOX].passed
        assert not checks[ReimportCheckId.REWRITTEN].passed
        [offender] = checks[ReimportCheckId.REWRITTEN].offenders
        assert offender == (
            f'{stale} — USDJPY_20260110_220000.parquet was written 2026-08-20 11:00:00 UTC, '
            f'before its extraction at '
            f"{tree.build_tool().read_manifest().extracted_at[stale].strftime('%Y-%m-%d %H:%M:%S')}"
            ' UTC')
        assert checks[ReimportCheckId.REWRITTEN].detail == '2 of 3 written after extraction'

    def test_names_are_compared_in_both_directions(self, tree):
        """One file only the archive holds and one only the index names: equal counts, red names."""
        _extract_winter(tree)
        tree.write_zip('late.zip', {
            'GBPUSD_20260610_000000_ticks.json': member_json('GBPUSD', 'mt5')})
        tree.index_member('EURUSD_20251201_000000_ticks.json', 'mt5', 'EURUSD',
                          WINTER_START, WINTER_START)
        tree.simulate_import()

        checks = _checks_by_id(tree)
        assert checks[ReimportCheckId.COUNT].passed
        assert not checks[ReimportCheckId.NAMES].passed
        assert checks[ReimportCheckId.NAMES].offenders == (
            'GBPUSD_20260610_000000_ticks.json — in the archive, not in the index',
            'EURUSD_20251201_000000_ticks.json — in the index, in no archive')

    def test_a_missing_parquet_fails_rewritten(self, tree):
        """An index row pointing at a parquet that is gone proves nothing."""
        _extract_winter(tree)
        tree.simulate_import()
        (tree.processed_dir / 'mt5' / 'ticks' / 'EURUSD'
         / 'EURUSD_20260115_040207.parquet').unlink()

        [offender] = _checks_by_id(tree)[ReimportCheckId.REWRITTEN].offenders
        assert offender == ('EURUSD_20260115_040207_ticks.json — its parquet '
                            'EURUSD_20260115_040207.parquet is missing')

    def test_without_a_manifest_verify_fails(self, tree, capsys):
        """Nothing extracted is nothing verified, and that is not a pass."""
        build_standard_archive(tree)

        assert tree.build_tool().verify() == 1
        assert 'nothing was extracted to verify' in capsys.readouterr().out


class TestClean:
    """Only behind a green verify, and only what is proven to be a copy."""

    def test_refuses_while_verify_is_red(self, tree, capsys):
        """Nothing is deleted and the manifest stays."""
        _extract_winter(tree)
        tree.simulate_import(refused=['GBPUSD_20260120_000000_ticks.json'])
        moved = sorted(path.name for path in tree.finished_dir.glob('*_ticks.json'))

        assert tree.build_tool().clean() == 1

        assert 'verify is not green, nothing was deleted' in capsys.readouterr().out
        assert sorted(path.name for path in tree.finished_dir.glob('*_ticks.json')) == moved
        assert tree.manifest_path.exists()

    def test_deletes_identical_copies_keeps_a_differing_one_and_never_a_source(self, tree,
                                                                              capsys):
        """The three outcomes side by side, and the archive untouched."""
        build_standard_archive(tree)
        source = 'EURUSD_20260116_000000_ticks.json'
        tree.write_loose(source, member_json('EURUSD', 'mt5', marker='not yet archived'))
        tree.index_member(source, 'mt5', 'EURUSD', WINTER_START + timedelta(days=75),
                          WINTER_START + timedelta(days=76))
        zips_before = {path.name: path.read_bytes() for path in tree.archives_dir.glob('*.zip')}
        assert tree.build_tool().extract(WINTER_SCOPE) == 0
        # A migration corrects one file in the inbox before the import.
        corrected = 'GBPUSD_20260120_000000_ticks.json'
        (tree.raw_dir / corrected).write_bytes(member_json('GBPUSD', 'mt5', marker='migrated'))
        tree.simulate_import(shift=timedelta(hours=1))

        assert tree.build_tool().clean() == 0

        left = sorted(path.name for path in tree.finished_dir.glob('*_ticks.json'))
        assert left == [source, corrected]
        out = capsys.readouterr().out
        assert '2 loose copies, SHA-256-equal to their archived member' in out
        assert '1 awaiting archiving — differs from its archived member' in out
        assert f'               {corrected}' in out
        assert '1 source files in no zip' in out
        assert not tree.manifest_path.exists()
        assert {path.name: path.read_bytes()
                for path in tree.archives_dir.glob('*.zip')} == zips_before

    def test_a_second_clean_has_nothing_to_do(self, tree):
        """Once the manifest is gone, clean refuses rather than guessing."""
        _extract_winter(tree)
        tree.simulate_import()
        assert tree.build_tool().clean() == 0

        assert tree.build_tool().clean() == 1
