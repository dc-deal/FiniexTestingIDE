"""
Raw Archive Catalog: what counts as a member of the archive, which broker a member belongs to,
and what a scope selects.

The archive is selected by MEMBER name, never by the zip's own name, because a batch zip is named
after one of its members and can hold another broker's files. The broker comes from the tick
index, else from the member's own header, else it stays unresolved and is never selected.
"""

import argparse
import zipfile
from datetime import datetime, timezone

import pytest

from python.experiments.raw_archive_reimport.raw_archive_reimport import (
    parse_symbols,
    parse_window,
)
from python.experiments.raw_archive_reimport.raw_archive_reimport_types import (
    BrokerSource,
    MemberOrigin,
    ReimportScope,
)
from tests.data.raw_archive_reimport.conftest import (
    ALL_MT5,
    WINTER_END,
    WINTER_MT5,
    WINTER_START,
    build_standard_archive,
    member_json,
)


def _utc(*parts: int) -> datetime:
    """
    Args:
        parts: year, month, day and optionally hour, minute, second

    Returns:
        The UTC-aware instant
    """
    return datetime(*parts, tzinfo=timezone.utc)


class TestSelectionByMemberName:
    """A zip's own name never decides what is selected."""

    def test_zip_named_after_a_kraken_member_still_yields_its_mt5_members(self, tree):
        """The XRPUSD batch zip holds mt5 files; they are selected for mt5, XRPUSD is not."""
        build_standard_archive(tree)
        selected = tree.build_catalog().select(ReimportScope('mt5'))

        assert sorted(member.name for member in selected) == sorted(ALL_MT5)
        from_batch_zip = sorted(member.name for member in selected
                                if member.container.name == 'XRPUSD_20260911_134741_ticks.zip')
        assert from_batch_zip == ['USDJPY_20260110_220000_ticks.json',
                                  'USDJPY_20260910_100000_ticks.json']

    def test_plan_flags_a_zip_named_after_another_brokers_member(self, tree, capsys):
        """The plan warns where selecting by the zip's name would have gone wrong."""
        build_standard_archive(tree)
        assert tree.build_tool().plan(ReimportScope('mt5')) == 0

        lines = capsys.readouterr().out.splitlines()
        batch_line = next(line for line in lines
                          if line.strip().startswith('XRPUSD_20260911_134741_ticks.zip'))
        assert 'named after a kraken_spot member' in batch_line
        bulk_line = next(line for line in lines
                         if line.strip().startswith('All_ticks_v3_restauration_ticks.zip'))
        assert 'named after' not in bulk_line

    def test_plan_reads_only(self, tree):
        """Plan writes nothing anywhere in the tree."""
        build_standard_archive(tree)
        before = sorted(str(path) for path in tree.root.rglob('*'))

        tree.build_tool().plan(ReimportScope('mt5', window_start=WINTER_START,
                                             window_end=WINTER_END))

        assert sorted(str(path) for path in tree.root.rglob('*')) == before

    def test_symbols_match_exactly(self, tree):
        """'EURUSD' selects EURUSD and nothing that merely starts with it."""
        tree.write_zip('batch.zip', {
            'EURUSD_20260115_000000_ticks.json': member_json('EURUSD', 'mt5'),
            'EURUSDm_20260115_000000_ticks.json': member_json('EURUSDm', 'mt5'),
        })
        selected = tree.build_catalog().select(ReimportScope('mt5', symbols=('EURUSD',)))

        assert [member.name for member in selected] == ['EURUSD_20260115_000000_ticks.json']

    def test_entries_that_are_not_tick_files_are_ignored_and_counted(self, tree):
        """A directory entry or a file of another shape is counted, never selected."""
        tree.write_zip('batch.zip', {
            'notes/': b'',
            'notes/readme.txt': b'hello',
            'EURUSD_20260115_ticks.json': member_json('EURUSD', 'mt5'),
            'nested/GBPUSD_20260115_000000_ticks.json': member_json('GBPUSD', 'mt5'),
        })
        catalog = tree.build_catalog()

        assert sorted(catalog.get_ignored_entries()) == [
            'batch.zip:EURUSD_20260115_ticks.json', 'batch.zip:notes/',
            'batch.zip:notes/readme.txt']
        members = catalog.get_members()
        assert [(member.name, member.entry_name) for member in members] == [
            ('GBPUSD_20260115_000000_ticks.json', 'nested/GBPUSD_20260115_000000_ticks.json')]

    def test_signal_archives_and_subdirectories_are_never_read(self, tree):
        """Only zips directly under the archives directory count, and never a signal zip."""
        tree.write_zip('signals-26-09-18.zip', {
            'EURUSD_20260115_000000_ticks.json': member_json('EURUSD', 'mt5')})
        nested = tree.archives_dir / 'signals'
        nested.mkdir()
        with zipfile.ZipFile(nested / 'forex.zip', 'w') as archive:
            archive.writestr('GBPUSD_20260115_000000_ticks.json', member_json('GBPUSD', 'mt5'))
        catalog = tree.build_catalog()

        assert catalog.get_members() == []
        assert catalog.get_archives() == []
        assert [path.name for path in catalog.get_skipped_archives()] == ['signals-26-09-18.zip']


class TestBrokerResolution:
    """Index first, member header second, never the symbol's shape."""

    def test_the_index_row_decides_over_the_header(self, tree):
        """A member the index files under mt5 is mt5, whatever its header claims."""
        name = 'EURUSD_20260115_000000_ticks.json'
        tree.write_zip('batch.zip', {name: member_json('EURUSD', 'kraken_spot')})
        tree.index_member(name, 'mt5', 'EURUSD', _utc(2026, 1, 15), _utc(2026, 1, 16))

        [member] = tree.build_catalog().get_members()

        assert (member.broker_type, member.broker_source) == ('mt5', BrokerSource.TICK_INDEX)
        assert member.index_start == _utc(2026, 1, 15)

    def test_the_header_decides_when_the_index_does_not_know_the_member(self, tree):
        """A forex-looking symbol whose header says kraken_spot is kraken_spot."""
        tree.write_zip('batch.zip', {
            'EURUSD_20260115_000000_ticks.json': member_json('EURUSD', 'kraken_spot')})

        [member] = tree.build_catalog().get_members()

        assert (member.broker_type, member.broker_source) == (
            'kraken_spot', BrokerSource.MEMBER_HEADER)
        assert member.index_start is None

    def test_the_older_data_collector_key_resolves(self, tree):
        """A header without broker_type is read by its data_collector, like the importer does."""
        tree.write_zip('batch.zip', {
            'EURUSD_20250920_000000_ticks.json': member_json(
                'EURUSD', 'mt5', header_key='data_collector')})

        [member] = tree.build_catalog().get_members()

        assert (member.broker_type, member.broker_source) == ('mt5', BrokerSource.MEMBER_HEADER)

    def test_broker_type_wins_over_data_collector(self, tree):
        """Where both keys are present, broker_type is the one read."""
        content = member_json('XRPUSD', 'kraken_spot').replace(
            b'"symbol": "XRPUSD"', b'"symbol": "XRPUSD", "data_collector": "kraken"')
        tree.write_zip('batch.zip', {'XRPUSD_20260911_134741_ticks.json': content})

        [member] = tree.build_catalog().get_members()

        assert member.broker_type == 'kraken_spot'

    def test_an_unknown_or_missing_broker_is_unresolved_and_never_selected(self, tree):
        """'MT5' is not a configured broker type, and a header naming none answers nothing."""
        tree.write_zip('batch.zip', {
            'EURUSD_20260115_000000_ticks.json': member_json('EURUSD', 'MT5'),
            'GBPUSD_20260115_000000_ticks.json': member_json('GBPUSD', None),
        })
        catalog = tree.build_catalog()

        assert {member.broker_source for member in catalog.get_members()} == {
            BrokerSource.UNRESOLVED}
        assert all(member.broker_type is None for member in catalog.get_members())
        assert catalog.select(ReimportScope('mt5')) == []

    def test_an_unresolved_member_the_scope_could_cover_blocks_plan_and_extract(self, tree,
                                                                               capsys):
        """Plan exits 1 and extract refuses: the member may belong to the scope."""
        build_standard_archive(tree)
        tree.write_zip('stray.zip', {
            'EURUSD_20260116_000000_ticks.json': member_json('EURUSD', None)})
        tool = tree.build_tool()

        assert tool.plan(ReimportScope('mt5')) == 1
        assert 'broker type unresolved' in capsys.readouterr().out
        assert tool.extract(ReimportScope('mt5')) == 1
        assert list(tree.raw_dir.iterdir()) == []

    def test_an_unresolved_member_outside_the_scope_does_not_block(self, tree):
        """An unresolved GBPUSD file does not concern a scope of EURUSD alone."""
        build_standard_archive(tree)
        tree.write_zip('stray.zip', {
            'GBPUSD_20260116_000000_ticks.json': member_json('GBPUSD', None)})

        assert tree.build_tool().plan(ReimportScope('mt5', symbols=('EURUSD',))) == 0

    def test_an_unknown_scope_broker_blocks(self, tree, capsys):
        """A broker type that is not configured is named, not silently selecting nothing."""
        build_standard_archive(tree)

        assert tree.build_tool().plan(ReimportScope('MT5')) == 1
        assert "unknown broker type 'MT5'" in capsys.readouterr().out


class TestDuplicateNames:
    """A name held twice leaves it open which copy is the truth."""

    def test_a_name_in_two_zips_blocks_plan_and_extract(self, tree, capsys):
        """Plan exits 1 naming both zips; extract writes nothing."""
        build_standard_archive(tree)
        tree.write_zip('second.zip', {
            'EURUSD_20260115_040207_ticks.json': member_json('EURUSD', 'mt5')})
        tool = tree.build_tool()

        assert tool.plan(ReimportScope('mt5')) == 1
        assert ('EURUSD_20260115_040207_ticks.json is held 2 times: '
                'All_ticks_v3_restauration_ticks.zip, second.zip') in capsys.readouterr().out
        assert tool.extract(ReimportScope('mt5')) == 1
        assert list(tree.raw_dir.iterdir()) == []

    def test_a_name_twice_in_one_zip_is_a_duplicate(self, tree):
        """Two entries of one name inside one zip are reported like two zips."""
        path = tree.archives_dir / 'batch.zip'
        with pytest.warns(UserWarning, match='Duplicate name'):
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('EURUSD_20260115_000000_ticks.json', member_json('EURUSD', 'mt5'))
                archive.writestr('EURUSD_20260115_000000_ticks.json', member_json('EURUSD', 'mt5'))

        duplicates = tree.build_catalog().get_duplicate_names()

        assert duplicates == {'EURUSD_20260115_000000_ticks.json': ['batch.zip', 'batch.zip']}


class TestLooseFiles:
    """A loose file in no zip is a source; one whose name a zip holds is a copy."""

    def test_a_loose_file_in_no_zip_is_a_source(self, tree):
        """It is a member of the archive like a zipped one, and selectable."""
        build_standard_archive(tree)
        tree.write_loose('EURUSD_20260116_000000_ticks.json', member_json('EURUSD', 'mt5'))
        catalog = tree.build_catalog()

        loose = [member for member in catalog.get_members()
                 if member.origin is MemberOrigin.LOOSE]
        assert [member.name for member in loose] == ['EURUSD_20260116_000000_ticks.json']
        assert 'EURUSD_20260116_000000_ticks.json' in {
            member.name for member in catalog.select(ReimportScope('mt5'))}

    def test_a_loose_file_whose_name_a_zip_holds_is_a_copy(self, tree):
        """It is not counted a second time and never selected."""
        build_standard_archive(tree)
        tree.write_loose('GBPUSD_20260120_000000_ticks.json', member_json('GBPUSD', 'mt5'))
        catalog = tree.build_catalog()

        assert [path.name for path in catalog.get_loose_copies()] == [
            'GBPUSD_20260120_000000_ticks.json']
        held = [member for member in catalog.get_members()
                if member.name == 'GBPUSD_20260120_000000_ticks.json']
        assert [member.origin for member in held] == [MemberOrigin.ZIP]


class TestWindow:
    """The index row's span decides; a member the index does not know falls back to its name."""

    def _build(self, tree) -> None:
        """
        Four members around the end of the US winter.

        Args:
            tree: The archive tree
        """
        tree.write_zip('batch.zip', {
            'EURUSD_20260309_000000_ticks.json': member_json('EURUSD', 'mt5'),
            'EURUSD_20260301_000000_ticks.json': member_json('EURUSD', 'mt5'),
            'GBPUSD_20260201_000000_ticks.json': member_json('GBPUSD', 'mt5'),
            'GBPUSD_20260401_000000_ticks.json': member_json('GBPUSD', 'mt5'),
        })
        # Named after a day outside the window, but its ticks begin inside it.
        tree.index_member('EURUSD_20260309_000000_ticks.json', 'mt5', 'EURUSD',
                          _utc(2026, 3, 8, 21), _utc(2026, 3, 9, 21))
        # Named after a day inside the window, but its row lies outside: the row decides.
        tree.index_member('EURUSD_20260301_000000_ticks.json', 'mt5', 'EURUSD',
                          _utc(2026, 3, 20), _utc(2026, 3, 21))

    def test_the_index_row_overlap_decides(self, tree):
        """Overlap of start..end as stored, not the name."""
        self._build(tree)
        selected = tree.build_catalog().select(
            ReimportScope('mt5', symbols=('EURUSD',), window_start=WINTER_START,
                          window_end=WINTER_END))

        assert [member.name for member in selected] == ['EURUSD_20260309_000000_ticks.json']

    def test_a_member_the_index_does_not_know_falls_back_to_its_name(self, tree, capsys):
        """The stamp in the name, read as one instant, decides; plan says which way each went."""
        self._build(tree)
        scope = ReimportScope('mt5', window_start=WINTER_START, window_end=WINTER_END)
        selected = tree.build_catalog().select(scope)

        assert sorted(member.name for member in selected) == [
            'EURUSD_20260309_000000_ticks.json', 'GBPUSD_20260201_000000_ticks.json']
        assert tree.build_tool().plan(scope) == 0
        assert ('window decided by the index row for 1, by the file name for 1'
                in capsys.readouterr().out)

    def test_the_standard_winter_selects_the_winter_mt5_files(self, tree):
        """The scope #562 re-imports, over the standard archive."""
        build_standard_archive(tree)
        selected = tree.build_catalog().select(
            ReimportScope('mt5', window_start=WINTER_START, window_end=WINTER_END))

        assert sorted(member.name for member in selected) == sorted(WINTER_MT5)


class TestArguments:
    """How a window and a symbol list are read from the command line."""

    def test_a_bare_end_date_covers_that_whole_day(self):
        """Both dates of 2025-11-02..2026-03-08 are inside the window."""
        assert parse_window('2025-11-02..2026-03-08') == (WINTER_START, WINTER_END)

    def test_an_explicit_time_is_taken_as_written(self):
        """A time without a zone is UTC, and it is not stretched to the end of its day."""
        assert parse_window('2025-11-02T06:00..2026-03-08T07:00') == (
            _utc(2025, 11, 2, 6), _utc(2026, 3, 8, 7))

    def test_either_side_may_be_open(self):
        """An empty side leaves that end of the window open."""
        assert parse_window('2025-11-02..') == (WINTER_START, None)
        assert parse_window('..2026-03-08') == (None, WINTER_END)

    @pytest.mark.parametrize('text', ['2025-11-02', '..', '2026-03-08..2025-11-02',
                                      'yesterday..today'])
    def test_an_unreadable_window_is_refused(self, text):
        """No separator, no side, a reversed window, or words: refused with a message."""
        with pytest.raises(argparse.ArgumentTypeError):
            parse_window(text)

    def test_symbols_are_read_without_blanks_and_repeats(self):
        """Order is kept; blanks and repeats are dropped."""
        assert parse_symbols(' EURUSD, GBPUSD,,EURUSD ') == ('EURUSD', 'GBPUSD')
        assert parse_symbols('') == ()
