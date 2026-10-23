"""
Store entry members (#621) — the declaration of what a run directory and a sweep directory may hold.

These tests read the declaration alone and take milliseconds. Whether the writers keep to it is the
census's question, and the census runs real runs.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from python.api.api_contract import API_CONTRACT_VERSION
from python.framework.constants.run_layout_constants import (
    IO_SUBDIR,
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.reporting.io import artifact_specs
from python.framework.reporting.io.report_artifact_io import ArtifactSpec
from python.framework.store.entry_members.run_entry_members import (
    RUN_ENTRY_MEMBERS,
    RUN_ENTRY_MEMBERS_BY_ID,
    RUN_INDEX_COLUMN_MEMBERS,
)
from python.framework.store.entry_members.sweep_entry_members import (
    SWEEP_ENTRY_MEMBERS,
    SWEEP_ENTRY_MEMBERS_BY_ID,
)
from python.framework.store.store_catalog import StoreCatalog
from python.framework.types.store_member_types import (
    EntryMemberMoment,
    EntryMemberShape,
    EntryMemberWriteMode,
    RunEntryMemberId,
    RunIndexColumn,
    SweepEntryMemberId,
)
from tests.shared.markdown_anchor_helpers import heading_anchors

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_RUN_TYPES = (RUN_TYPE_SIMULATION, RUN_TYPE_AUTOTRADER)
_ALL_MEMBERS = RUN_ENTRY_MEMBERS + SWEEP_ENTRY_MEMBERS

# How each shape may reach the disk: a stream and a log append lines, a report output is written
# once at the run's end, a snapshot is replaced whole — atomically, or in place where nothing reads
# it while it is written — and a strategy's own file is replaced whole, atomically, whenever the
# strategy writes it while the run goes on.
_WRITE_MODES_BY_SHAPE = {
    EntryMemberShape.STREAM: {EntryMemberWriteMode.APPEND_LINES},
    EntryMemberShape.LOG: {EntryMemberWriteMode.APPEND_LINES},
    EntryMemberShape.REPORT_OUTPUT: {EntryMemberWriteMode.IN_PLACE},
    EntryMemberShape.SNAPSHOT: {EntryMemberWriteMode.ATOMIC_REPLACE, EntryMemberWriteMode.IN_PLACE},
    EntryMemberShape.STRATEGY_OUTPUT: {EntryMemberWriteMode.ATOMIC_REPLACE},
}

# The file names the run list serves in each index column. Changing one changes what a consumer of
# the run list reads, so they are pinned here as literals rather than derived from the constants: a
# renamed constant would carry a derived pin along with it. The declaration may grow, the contract
# moves only on purpose.
_SERVED_BY_COLUMN = {
    RunIndexColumn.STREAM_FILES: {'order_events.jsonl', 'state_frames.jsonl'},
    RunIndexColumn.ARTIFACTS: {
        'aggregated_portfolio.json', 'block_splitting.json', 'booking_periods.json', 'broker.json',
        'cold_start.json', 'execution_stats.json', 'feed_stability.json', 'order_history.json',
        'pending_orders.json', 'portfolio.json', 'profiling.json', 'robustness.json',
        'run_meta.json', 'run_summary.json', 'safety.json', 'scenario_details.json', 'signal.json',
        'trade_history.json', 'venue_account.json', 'warnings_errors.json',
        'worker_decision.json', 'execution_stats.csv', 'order_history.csv', 'trade_history.csv',
    },
}

# What the declaration must never load: the report models, the reporting machinery, a
# configuration manager, a logger, the data libraries.
_HEAVY_MODULE_PREFIXES = ('python.framework.types.api', 'python.framework.reporting',
                          'python.configuration', 'python.framework.logging', 'pydantic',
                          'pandas', 'pyarrow', 'numpy')


class TestTheDeclarationIsComplete:
    """Every member id has its member, and every report spec is declared."""

    def test_every_run_member_id_is_declared_once(self):
        ids = [member.member_id for member in RUN_ENTRY_MEMBERS]
        assert len(ids) == len(set(ids)), 'a member is declared twice'
        assert set(ids) == set(RunEntryMemberId), set(RunEntryMemberId) ^ set(ids)
        assert set(RUN_ENTRY_MEMBERS_BY_ID) == set(RunEntryMemberId)

    def test_every_sweep_member_id_is_declared_once(self):
        ids = [member.member_id for member in SWEEP_ENTRY_MEMBERS]
        assert len(ids) == len(set(ids)), 'a member is declared twice'
        assert set(ids) == set(SweepEntryMemberId), set(SweepEntryMemberId) ^ set(ids)
        assert set(SWEEP_ENTRY_MEMBERS_BY_ID) == set(SweepEntryMemberId)

    def test_every_report_spec_is_declared_once_in_io(self):
        specs = [value for value in vars(artifact_specs).values()
                 if isinstance(value, ArtifactSpec)]
        assert specs
        patterns = [member.path_pattern for member in RUN_ENTRY_MEMBERS]
        for spec in specs:
            assert patterns.count(f'{IO_SUBDIR}/{spec.filename}') == 1, spec.filename

    def test_a_store_holding_another_stores_entries_points_at_one_that_declares_them(self):
        catalog = StoreCatalog()
        for descriptor in catalog.all():
            if descriptor.contains_entries_of is not None:
                assert descriptor.entry_members, descriptor.store_id
                assert catalog.get(descriptor.contains_entries_of).entry_members, (
                    f'{descriptor.store_id} holds entries of {descriptor.contains_entries_of}, '
                    f'which declares no members')


class TestEveryPathHasOneMember:
    """A file inside an entry is claimed by exactly one member of its pipeline."""

    def test_every_example_is_matched_by_its_own_member(self):
        for member in _ALL_MEMBERS:
            assert member.matches(member.example()), member.member_id

    @pytest.mark.parametrize('run_type', _RUN_TYPES)
    def test_no_two_members_of_one_pipeline_claim_one_path(self, run_type):
        members = [member for member in RUN_ENTRY_MEMBERS if member.is_written_by(run_type)]
        assert members
        for member in members:
            claims = [other.member_id for other in members if other.matches(member.example())]
            assert claims == [member.member_id], f'{member.example()} is claimed by {claims}'

    def test_no_two_sweep_members_claim_one_path(self):
        for member in SWEEP_ENTRY_MEMBERS:
            claims = [other.member_id for other in SWEEP_ENTRY_MEMBERS
                      if other.matches(member.example())]
            assert claims == [member.member_id], f'{member.example()} is claimed by {claims}'

    def test_a_placeholder_never_spans_a_directory(self):
        # Each path differs from a match only where the placeholder stands, so only a placeholder
        # that swallowed a slash could claim it.
        events = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.SCENARIO_EVENTS]
        assert events.matches('events/events_window_01.csv')
        assert not events.matches('events/events_window/01.csv')
        diagnostics = RUN_ENTRY_MEMBERS_BY_ID[RunEntryMemberId.SESSION_DIAGNOSTICS]
        assert diagnostics.matches('diagnostics/entry_signals.csv')
        assert not diagnostics.matches('diagnostics/entry/signals.csv')

    def test_every_member_is_written_by_a_known_pipeline(self):
        for member in _ALL_MEMBERS:
            assert member.written_by, member.member_id
            assert all(write.run_type in _RUN_TYPES
                       for write in member.written_by), member.member_id


class TestWhatTheDeclarationPromises:
    """What a member's declaration says about how it is written and listed."""

    def test_only_a_member_written_at_the_runs_end_is_listed_among_its_artifacts(self):
        """
        The rule the old deny-list could break: a file written before the report, listed as an
        artifact, makes a session that died before its report read as reported.
        """
        listed = [member for member in RUN_ENTRY_MEMBERS
                  if member.index_column is RunIndexColumn.ARTIFACTS]
        assert listed
        for member in listed:
            moments = {write.moment for write in member.written_by}
            assert moments == {EntryMemberMoment.RUN_END}, member.member_id

    def test_only_a_stream_is_listed_among_the_stream_files(self):
        for member in RUN_ENTRY_MEMBERS:
            if member.index_column is RunIndexColumn.STREAM_FILES:
                assert member.shape is EntryMemberShape.STREAM, member.member_id

    def test_a_listed_member_lies_directly_in_io(self):
        """The run index lists the file names it finds in io/; a member elsewhere it never sees."""
        for members in RUN_INDEX_COLUMN_MEMBERS.values():
            for member in members:
                folder, _, name = member.path_pattern.partition('/')
                assert folder == IO_SUBDIR and name and '/' not in name, member.member_id

    @pytest.mark.parametrize('column', list(RunIndexColumn))
    def test_what_the_run_list_serves_changes_only_with_the_contract(self, column):
        listed = {member.path_pattern.split('/', 1)[1]
                  for member in RUN_INDEX_COLUMN_MEMBERS[column]}
        assert listed == _SERVED_BY_COLUMN[column], (
            f'`{column}` on the run list would change by '
            f'{sorted(listed ^ _SERVED_BY_COLUMN[column])}'
            f' — that changes what the API serves: raise API_CONTRACT_VERSION to '
            f'{API_CONTRACT_VERSION + 1} with its entry in the contract log, then update this pin')

    def test_a_stream_declares_its_schema_version_and_nothing_else_does(self):
        for member in _ALL_MEMBERS:
            is_stream = member.shape is EntryMemberShape.STREAM
            assert (member.schema_version is not None) == is_stream, member.member_id

    def test_every_member_is_written_the_way_its_shape_allows(self):
        for member in _ALL_MEMBERS:
            assert member.write_mode in _WRITE_MODES_BY_SHAPE[member.shape], member.member_id

    def test_every_member_says_what_it_holds(self):
        for member in _ALL_MEMBERS:
            assert member.purpose.strip(), member.member_id

    def test_every_help_link_reaches_a_real_heading(self):
        for member in _ALL_MEMBERS:
            path, _, anchor = member.doc.partition('#')
            doc = _PROJECT_ROOT / path
            assert doc.is_file(), f'{member.member_id}: {path} does not exist'
            assert anchor in heading_anchors(doc), (
                f'{member.member_id}: no heading #{anchor} in {path}')


class TestTheDeclarationIsLight:
    """Every writer may import the declaration, so it may import nothing that imports them."""

    def test_importing_the_declaration_loads_no_model_configuration_or_logger(self):
        """
        Measured 2026-10-22: through the report specs the declaration would have loaded 719
        modules in 1.6 s, and the report writer, the run stream appender and the logger base could
        never have imported it without an import cycle.
        """
        code = ('import sys\n'
                'import python.framework.store.entry_members.run_entry_members\n'
                'import python.framework.store.entry_members.sweep_entry_members\n'
                'print("\\n".join(sorted(sys.modules)))\n')
        result = subprocess.run(
            [sys.executable, '-c', code], cwd=_PROJECT_ROOT, capture_output=True, text=True,
            env={**os.environ, 'PYTHONPATH': str(_PROJECT_ROOT)}, check=True)
        heavy = [name for name in result.stdout.split() if name.startswith(_HEAVY_MODULE_PREFIXES)]
        assert not heavy, f'the declaration loads {heavy}'
