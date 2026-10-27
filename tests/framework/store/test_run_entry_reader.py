"""
The run entry reader (#621): one run's files by member, the run found by an exact id, the file asked
of the disk.
"""

from datetime import datetime, timezone

import pytest

from python.framework.constants.run_layout_constants import IO_SUBDIR, RUN_TYPE_AUTOTRADER
from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.reporting.io import artifact_specs
from python.framework.reporting.io.artifact_specs import RUN_SUMMARY_ARTIFACT, ArtifactSpec
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.entry_members.run_entry_reader import RunEntryReader, section_member
from python.framework.types.api.report_types import RunHeader, RunSummary
from python.framework.types.store_member_types import RunEntryMemberId

_RUN_ID = '20261023_110000_bbbbbbbb'
_START = datetime(2026, 10, 23, 11, 0, tzinfo=timezone.utc)


@pytest.fixture
def indexed_run(tmp_path) -> tuple:
    """A run the index knows, with its header and nothing else."""
    index_path = tmp_path / 'runs_index.parquet'
    run_dir = tmp_path / 'autotrader' / 'my_bot' / _RUN_ID
    header = RunHeader(run_id=_RUN_ID, start_time=_START, run_type=RUN_TYPE_AUTOTRADER,
                       run_name='my_bot')
    write_run_header(header, run_dir)
    RunIndex(index_path).register_run(header, run_dir)
    return RunEntryReader(index_path), run_dir


class TestTheRunIsFoundByItsId:

    def test_an_unknown_run_has_nothing(self, indexed_run):
        reader, _ = indexed_run
        assert reader.run_dir('20261023_110000_cccccccc') is None
        assert reader.has('20261023_110000_cccccccc', RunEntryMemberId.HEADER) is False

    def test_a_pattern_for_an_id_matches_no_run(self, indexed_run):
        reader, _ = indexed_run
        assert reader.run_dir('*') is None

    def test_a_run_that_registers_after_the_first_ask_is_found(self, indexed_run, tmp_path):
        reader, _ = indexed_run
        later_id = '20261023_110000_dddddddd'
        assert reader.run_dir(later_id) is None
        header = RunHeader(run_id=later_id, start_time=_START, run_type=RUN_TYPE_AUTOTRADER,
                           run_name='my_bot')
        later_dir = tmp_path / 'autotrader' / 'my_bot' / later_id
        write_run_header(header, later_dir)
        RunIndex(tmp_path / 'runs_index.parquet').register_run(header, later_dir)

        assert reader.has(later_id, RunEntryMemberId.HEADER)

    def test_several_runs_are_looked_up_in_one_read(self, indexed_run, monkeypatch):
        reader, _ = indexed_run
        calls = []
        real = RunIndex.read
        monkeypatch.setattr(RunIndex, 'read', lambda self: calls.append(1) or real(self))
        reader.remember_run_dirs([_RUN_ID, '20261023_110000_cccccccc'])
        reader.run_dir(_RUN_ID)
        reader.has(_RUN_ID, RunEntryMemberId.HEADER)
        assert len(calls) == 1


class TestWhetherAFileIsThereIsAskedOfTheDisk:

    def test_a_section_written_before_the_index_lists_it_is_there(self, indexed_run):
        """The index stamps its lists when the reports land; the file is there before that."""
        reader, run_dir = indexed_run
        (run_dir / IO_SUBDIR).mkdir()
        (run_dir / IO_SUBDIR / RUN_SUMMARY_ARTIFACT.filename).write_text('{}', encoding='utf-8')

        assert reader.has(_RUN_ID, RunEntryMemberId.RUN_SUMMARY)

    def test_an_absent_section_reads_as_none(self, indexed_run):
        reader, _ = indexed_run
        assert reader.read_section(_RUN_ID, RUN_SUMMARY_ARTIFACT) is None
        assert reader.read_stream(_RUN_ID, RunEntryMemberId.ORDER_EVENTS) is None
        assert reader.read_stream_ends(_RUN_ID, RunEntryMemberId.ORDER_EVENTS) is None

    def test_a_section_and_the_header_read_back(self, indexed_run):
        reader, run_dir = indexed_run
        summary = RunSummary(run_id=_RUN_ID, currencies=[], orders_submitted=0,
                             orders_executed=0, orders_rejected=0, sl_tp_triggered=0, unit_count=1)
        (run_dir / IO_SUBDIR).mkdir()
        (run_dir / IO_SUBDIR / RUN_SUMMARY_ARTIFACT.filename).write_text(
            summary.model_dump_json(), encoding='utf-8')

        assert reader.read_section(_RUN_ID, RUN_SUMMARY_ARTIFACT).run_id == _RUN_ID
        assert reader.read_header(_RUN_ID).run_name == 'my_bot'


class TestAPathIsBuiltOnlyFromTheDeclaration:

    def test_a_member_with_name_parts_takes_them(self, indexed_run):
        reader, run_dir = indexed_run
        path = reader.path_of(_RUN_ID, RunEntryMemberId.SESSION_DIAGNOSTICS, sink='entry_signals')
        assert path == run_dir / 'diagnostics' / 'entry_signals.csv'

    @pytest.mark.parametrize('names', [
        {},
        {'sink': ''},
        {'sink': '../header'},
        {'sink': 'a/b'},
        {'sink': '..'},
        {'sink': 'ok', 'scenario': 'extra'},
    ])
    def test_a_missing_or_reaching_name_part_is_refused(self, indexed_run, names):
        reader, _ = indexed_run
        with pytest.raises(EntryMemberRefusedError):
            reader.path_of(_RUN_ID, RunEntryMemberId.SESSION_DIAGNOSTICS, **names)

    def test_every_report_section_is_a_member(self):
        specs = [value for value in vars(artifact_specs).values()
                 if isinstance(value, ArtifactSpec)]
        assert specs
        for spec in specs:
            assert section_member(spec).path_pattern == f'{IO_SUBDIR}/{spec.filename}'

    def test_a_section_nobody_declared_is_refused(self):
        with pytest.raises(EntryMemberRefusedError):
            section_member(ArtifactSpec('notes.json', RunSummary))
