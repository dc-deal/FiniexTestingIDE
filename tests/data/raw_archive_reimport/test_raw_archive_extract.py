"""
Raw Archive Re-import, extract: what reaches the importer's inbox, what the manifest records, and
every reason extract refuses to start.

Extract is the phase that writes. It streams a zip member to '<name>.part' and renames it only
after the stream has ended, which is where zipfile checks the member's CRC; a loose source is
moved, never copied.
"""

import shutil
import zipfile
from datetime import timezone
from types import SimpleNamespace

import pytest

from python.experiments.raw_archive_reimport.raw_archive_reimport_types import (
    BrokerSource,
    MemberOrigin,
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


def _member_bytes(tree, name: str) -> bytes:
    """
    Read a member's bytes straight from whichever zip holds it.

    Args:
        tree: The archive tree
        name: Member name

    Returns:
        The member's content
    """
    for path in tree.archives_dir.glob('*.zip'):
        with zipfile.ZipFile(path) as archive:
            if name in archive.namelist():
                return archive.read(name)
    raise AssertionError(f'{name} is in no zip')


class TestExtract:
    """What extract writes when nothing stands in its way."""

    def test_streams_the_selected_members_and_writes_the_manifest(self, tree):
        """The winter mt5 files land byte-identical in the inbox; the manifest says so."""
        build_standard_archive(tree)
        tool = tree.build_tool()

        assert tool.extract(WINTER_SCOPE) == 0

        inbox = sorted(path.name for path in tree.raw_dir.glob('*_ticks.json'))
        assert inbox == sorted(WINTER_MT5)
        assert list(tree.raw_dir.glob('*.part')) == []
        for name in inbox:
            assert (tree.raw_dir / name).read_bytes() == _member_bytes(tree, name)

        manifest = tool.read_manifest()
        assert manifest.scope == WINTER_SCOPE
        assert sorted(member.name for member in manifest.members) == sorted(WINTER_MT5)
        assert sorted(manifest.extracted_at) == sorted(WINTER_MT5)
        assert all(stamp.tzinfo is timezone.utc for stamp in manifest.extracted_at.values())
        assert all(stamp >= manifest.created_at for stamp in manifest.extracted_at.values())
        [before] = manifest.before['EURUSD_20260115_040207_ticks.json']
        assert (before.start_time.isoformat(), before.tick_count) == (
            '2026-01-15T01:02:07+00:00', 3)

    def test_the_archive_is_left_as_it_was(self, tree):
        """Nothing is written into a zip and nothing is taken out of one."""
        build_standard_archive(tree)
        before = {path.name: path.read_bytes() for path in tree.archives_dir.glob('*.zip')}

        assert tree.build_tool().extract(WINTER_SCOPE) == 0

        assert {path.name: path.read_bytes()
                for path in tree.archives_dir.glob('*.zip')} == before

    def test_the_manifest_reads_back_as_it_was_written(self, tree):
        """Members, their origin and broker source survive the round trip through JSON."""
        build_standard_archive(tree)
        tool = tree.build_tool()
        assert tool.extract(WINTER_SCOPE) == 0

        members = {member.name: member for member in tool.read_manifest().members}
        usdjpy = members['USDJPY_20260110_220000_ticks.json']
        assert usdjpy.container.name == 'XRPUSD_20260911_134741_ticks.zip'
        assert (usdjpy.origin, usdjpy.broker_source) == (MemberOrigin.ZIP,
                                                        BrokerSource.TICK_INDEX)
        assert usdjpy.size_bytes == len(_member_bytes(tree, usdjpy.name))

    def test_a_loose_source_is_moved_never_copied(self, tree):
        """The only copy there is leaves the finished directory for the inbox."""
        build_standard_archive(tree)
        name = 'EURUSD_20260116_000000_ticks.json'
        content = member_json('EURUSD', 'mt5', marker='collected after the last batch zip')
        loose = tree.write_loose(name, content)
        tool = tree.build_tool()

        assert tool.extract(WINTER_SCOPE) == 0

        assert not loose.exists()
        assert (tree.raw_dir / name).read_bytes() == content
        [member] = [member for member in tool.read_manifest().members if member.name == name]
        assert member.origin is MemberOrigin.LOOSE

    def test_symbols_batch_the_extraction(self, tree):
        """A scope naming one symbol extracts that symbol alone."""
        build_standard_archive(tree)

        assert tree.build_tool().extract(ReimportScope(
            'mt5', symbols=('USDJPY',), window_start=WINTER_START, window_end=WINTER_END)) == 0

        assert [path.name for path in tree.raw_dir.glob('*_ticks.json')] == [
            'USDJPY_20260110_220000_ticks.json']

    def test_a_member_failing_its_checksum_never_reaches_its_final_name(self, tree):
        """The CRC is checked at the end of the stream; the partial file is removed."""
        name = 'EURUSD_20260115_000000_ticks.json'
        content = member_json('EURUSD', 'mt5', tick_count=40)
        path = tree.write_zip('batch.zip', {name: content}, compression=zipfile.ZIP_STORED)
        tree.index_member(name, 'mt5', 'EURUSD', WINTER_START, WINTER_START)
        damaged = bytearray(path.read_bytes())
        damaged[damaged.find(content) + len(content) // 2] ^= 0xFF
        path.write_bytes(bytes(damaged))
        tool = tree.build_tool()

        with pytest.raises(zipfile.BadZipFile):
            tool.extract(ReimportScope('mt5'))

        assert not (tree.raw_dir / name).exists()
        assert not (tree.raw_dir / f'{name}.part').exists()
        assert tool.read_manifest().extracted_at == {}


class TestExtractRefuses:
    """Every refusal leaves the tree exactly as it was."""

    def test_an_inbox_already_holding_a_tick_file(self, tree, capsys):
        """A leftover could not be told apart from an extracted file."""
        build_standard_archive(tree)
        stray = tree.raw_dir / 'AUDUSD_20260101_000000_ticks.json'
        stray.write_bytes(b'{}')

        assert tree.build_tool().extract(WINTER_SCOPE) == 1

        assert 'could not be attributed' in capsys.readouterr().out
        assert list(tree.raw_dir.iterdir()) == [stray]

    def test_a_partial_extraction_left_in_the_inbox(self, tree):
        """A '.part' is an extraction that was interrupted."""
        build_standard_archive(tree)
        partial = tree.raw_dir / 'EURUSD_20260115_040207_ticks.json.part'
        partial.write_bytes(b'{')

        assert tree.build_tool().extract(WINTER_SCOPE) == 1
        assert list(tree.raw_dir.iterdir()) == [partial]

    def test_while_a_manifest_exists(self, tree, capsys):
        """A second extract cannot start while a re-import is in progress."""
        build_standard_archive(tree)
        tree.manifest_path.write_text('{}', encoding='utf-8')

        assert tree.build_tool().extract(WINTER_SCOPE) == 1
        assert 'a re-import is in progress' in capsys.readouterr().out
        assert list(tree.raw_dir.iterdir()) == [tree.manifest_path]

    def test_when_the_disk_is_too_small(self, tree, monkeypatch, capsys):
        """Free disk must cover the selected bytes with headroom."""
        build_standard_archive(tree)
        monkeypatch.setattr(shutil, 'disk_usage',
                            lambda path: SimpleNamespace(total=0, used=0, free=0))

        assert tree.build_tool().extract(WINTER_SCOPE) == 1
        assert 'select fewer symbols per batch' in capsys.readouterr().out
        assert list(tree.raw_dir.iterdir()) == []

    def test_when_a_selected_member_already_lies_loose(self, tree, capsys):
        """The import would replace that loose file, and it may hold what the zip does not."""
        build_standard_archive(tree)
        tree.write_loose('GBPUSD_20260120_000000_ticks.json',
                         member_json('GBPUSD', 'mt5', marker='corrected, not yet archived'))

        assert tree.build_tool().extract(WINTER_SCOPE) == 1
        assert 'already lie loose' in capsys.readouterr().out
        assert list(tree.raw_dir.iterdir()) == []

    def test_when_nothing_is_selected(self, tree, capsys):
        """An empty selection is not a re-import."""
        build_standard_archive(tree)

        assert tree.build_tool().extract(ReimportScope('mt5', symbols=('NZDUSD',))) == 1
        assert 'nothing is selected' in capsys.readouterr().out
        assert list(tree.raw_dir.iterdir()) == []
