"""
Store catalog views — what `store_cli.py catalog` and `rebuild` print.

The views only format: rows come from `StoreCatalog.status()`, advisories from the store health
checks. What is held here is that each part of a row reaches the reader in its place, that a stale
index is told apart from one that refreshes itself, and that an advisory is shown under its check's
title with the gate or broker it concerns.
"""

from typing import List

from python.framework.reporting.console.store_catalog_summary import (
    render_store_catalog,
    render_store_rebuild_refused,
    render_store_rebuild_skipped,
    render_store_rebuilt,
)
from python.framework.types.store_types import (
    RetrievalForm,
    StoreBackend,
    StoreId,
    StoreKind,
    StoreStatus,
)
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS_BY_ID


def _row(store_id: StoreId = StoreId.RUNS, **overrides) -> StoreStatus:
    """
    One status row with plain values, overridable per field.

    Args:
        store_id: Which store the row describes
        overrides: Fields to set differently

    Returns:
        The row
    """
    values = dict(
        store_id=store_id, kind=StoreKind.RECORD, purpose=f'what {store_id.value} is for',
        doc=f'docs/{store_id.value}.md#heading', root=store_id.value,
        key=f'{store_id.value} key', form=RetrievalForm.DOCUMENT, backend=StoreBackend.DISK,
        index_name=f'{store_id.value}_index.parquet', note='', entries=3, size_bytes=None,
        exists=True)
    values.update(overrides)
    return StoreStatus(**values)


def _finding(check: str, scope: str, message: str) -> ValidationFinding:
    """
    One advisory.

    Args:
        check: The check id
        scope: The gate or broker it concerns
        message: Its text

    Returns:
        The finding
    """
    return ValidationFinding(severity=Severity.WARNING, check=check,
                             domain=ValidationDomain.RELEASE, message=message, scope=scope)


def _render(capsys, rows: List[StoreStatus], findings: List[ValidationFinding] = (),
            with_sizes: bool = False) -> str:
    """
    The catalog view as printed.

    Args:
        capsys: pytest's output capture
        rows: The rows
        findings: The advisories
        with_sizes: Whether sizes are shown

    Returns:
        Everything printed
    """
    render_store_catalog(rows, list(findings), with_sizes)
    return capsys.readouterr().out


class TestTheTable:
    def test_every_store_gets_its_line_its_key_and_its_help_link(self, capsys):
        out = _render(capsys, [_row(StoreId.RUNS), _row(StoreId.RUN_LEDGER)])

        for store in ('runs', 'run_ledger'):
            line = next(ln for ln in out.splitlines() if f'what {store} is for' in ln)
            assert f'{store}_index.parquet' in line, 'the purpose sits on the store\'s own row'
        key_block = out.split('How ONE entry is addressed:')[1].split('Where each store')[0]
        assert 'runs key' in key_block and 'run_ledger key' in key_block
        help_block = out.split('Where each store is explained:')[1]
        assert 'docs/runs.md#heading' in help_block and 'docs/run_ledger.md#heading' in help_block

    def test_an_absent_root_and_an_uncountable_store_say_so(self, capsys):
        out = _render(capsys, [_row(exists=False, entries=None, index_name=None)])
        line = next(ln for ln in out.splitlines() if 'what runs is for' in ln)
        assert 'runs (absent)' in line
        assert '—' in line, 'no index and no count read as a dash, never as zero'

    def test_the_size_column_appears_only_when_measured(self, capsys):
        assert 'SIZE' not in _render(capsys, [_row()])

        out = _render(capsys, [_row(size_bytes=2048), _row(StoreId.RUN_LEDGER)], with_sizes=True)
        assert 'SIZE' in out
        assert '2.0 KB' in next(ln for ln in out.splitlines() if 'what runs is for' in ln)
        assert '—' in next(ln for ln in out.splitlines() if 'what run_ledger is for' in ln)


class TestStaleness:
    def test_a_stale_index_is_a_task_and_a_self_healing_one_is_not(self, capsys):
        out = _render(capsys, [
            _row(StoreId.RUNS, stale_reason='never built'),
            _row(StoreId.RUN_LEDGER, stale_reason='a fragment is newer', self_healing=True)])

        assert '⚠ stale' in next(ln for ln in out.splitlines() if 'what runs is for' in ln)
        assert '↻ refreshes on read' in next(
            ln for ln in out.splitlines() if 'what run_ledger is for' in ln)
        stale_block = out.split('Stale index')[1].split('Behind, but')[0]
        assert 'never built' in stale_block and 'a fragment is newer' not in stale_block
        assert 'a fragment is newer' in out.split('Behind, but')[1]

    def test_a_stale_index_whose_rebuild_loses_data_is_not_sent_to_a_rebuild(self, capsys):
        """The plain advice would be to rebuild — for this store that advice is a deletion."""
        out = _render(capsys, [_row(StoreId.RUN_CONFIGS, stale_reason='1 version has no copy',
                                    rebuild_loses='when each version was first seen')])
        assert 'Stale index — rebuild before trusting it' not in out
        lossy = out.split('rebuild LOSES information')[1]
        assert '1 version has no copy' in lossy and 'when each version was first seen' in lossy

    def test_a_valid_catalog_prints_neither_block(self, capsys):
        out = _render(capsys, [_row()])
        assert 'Stale index' not in out and 'Behind, but' not in out


class TestAdvisories:
    def test_findings_render_under_their_check_title_with_their_scope(self, capsys):
        out = _render(capsys, [_row()], [
            _finding('certificate_expired', 'benchmark', 'valid until 2026-12-14'),
            _finding('fee_structure_frozen_long', 'kraken_spot', 'frozen 2026-05-01'),
            _finding('certificate_expired', 'live_adapters', 'valid until 2026-12-09')])

        certificate_title = VALIDATION_CHECKS_BY_ID['certificate_expired'].title
        fee_title = VALIDATION_CHECKS_BY_ID['fee_structure_frozen_long'].title
        assert out.count(certificate_title) == 1, 'one block per check, however many findings'
        certificates = out.split(certificate_title)[1].split(fee_title)[0]
        assert 'benchmark' in certificates and 'live_adapters' in certificates
        assert 'kraken_spot' in out.split(fee_title)[1]
        assert out.index(certificate_title) < out.index(fee_title), 'in the order produced'

    def test_no_findings_print_no_advisory(self, capsys):
        assert '⏰' not in _render(capsys, [_row()])


class TestNotes:
    def test_a_stated_reason_is_listed_and_an_empty_one_is_not(self, capsys):
        out = _render(capsys, [_row(StoreId.RUNS, note='why it has no index'),
                               _row(StoreId.RUN_LEDGER)])
        notes = out.split('Notes')[1]
        assert 'why it has no index' in notes and 'run_ledger' not in notes

    def test_no_notes_no_heading(self, capsys):
        assert 'Notes' not in _render(capsys, [_row()])


class TestRebuild:
    def test_a_rebuild_names_the_store_and_the_count(self, capsys):
        render_store_rebuilt(StoreId.RUNS, 40)
        assert 'runs' in (out := capsys.readouterr().out) and '40' in out

    def test_a_skipped_store_says_what_its_rebuild_would_lose_and_how_to_run_it(self, capsys):
        render_store_rebuild_skipped(StoreId.RUN_CONFIGS, 'when each version was first seen')
        out = capsys.readouterr().out
        assert 'run_configs' in out and 'skipped' in out
        assert 'when each version was first seen' in out and '--accept-loss' in out

    def test_a_refusal_names_the_store_and_the_reason(self, capsys):
        render_store_rebuild_refused(StoreId.TICKS, 'no index this model owns')
        out = capsys.readouterr().out
        assert 'ticks' in out and 'no index this model owns' in out
