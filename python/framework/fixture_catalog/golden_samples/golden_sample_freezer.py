"""
Golden sample freezer (#557) — the current production of a fixture catalog entry, frozen.

The aggregation refactor has to change no number, and the proof is a set of runs whose aggregates
are frozen before anything moves. The fixture catalog already reproduces such runs on demand;
this freezes the newest verified production of an entry into the committed golden samples: the
report sections and ledger rows every aggregate is computed from, the read-time views the API
composes from them, and the code it all came from.

It writes nothing doubtful. Before a sample replaces the one in place, it is written to a scratch
folder, read back, and every aggregate is rebuilt from what was read and compared field by field
with what the run itself recorded — the read-time views with what the real stores answer. A field
that is not reproduced refuses the freeze. So does a production whose code no commit names,
unless the caller allows it for a sample that is not committed.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.framework.constants.golden_sample_constants import GOLDEN_SAMPLES_DIR
from python.framework.exceptions.golden_sample_errors import (
    GoldenSampleDirtyProductionError,
    GoldenSampleNoProductionError,
    GoldenSampleNotReproducedError,
)
from python.framework.fixture_catalog.fixture_production_store import FixtureProductionStore
from python.framework.fixture_catalog.golden_samples.golden_sample_checks import (
    FROZEN_SECTIONS,
    GOLDEN_SAMPLE_BLOCKS,
    block_applies,
    check_golden_sample,
    compose_views,
)
from python.framework.fixture_catalog.golden_samples.golden_sample_io import (
    load_golden_sample,
    write_golden_sample,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.types.fixture_catalog_types import FixtureEntry, FixtureProducerKind
from python.framework.types.golden_sample_types import (
    FrozenRun,
    GoldenSample,
    GoldenSampleFreeze,
    GoldenSampleHeader,
    GoldenSampleInputs,
    GoldenSampleRun,
)


class GoldenSampleFreezer:
    """Freezes the current production of a catalog entry into its golden sample."""

    def __init__(
        self,
        store: FixtureProductionStore,
        root: Path = GOLDEN_SAMPLES_DIR,
        run_entries: Optional[RunEntryReader] = None,
        ledger: Optional[RunResultsLedger] = None,
        run_index: Optional[RunIndex] = None,
    ):
        """
        Args:
            store: The production record — it names each entry's current production
            root: The golden samples root
            run_entries: Reads a run's header and sections; the configured run index when omitted
            ledger: The run-results ledger; the configured one when omitted
            run_index: The run index; the configured one when omitted
        """
        self._store = store
        self._root = Path(root)
        self._run_entries = run_entries or RunEntryReader()
        file_logging = AppConfigManager().get_file_logging_config_object()
        self._ledger = ledger or RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))
        self._run_index = run_index or RunIndex(file_logging.run_index, file_logging.run_logs)

    def freeze(self, entry: FixtureEntry, allow_dirty: bool = False) -> GoldenSampleFreeze:
        """
        Freeze an entry's current production, after checking that it rebuilds.

        Args:
            entry: The catalog entry
            allow_dirty: Freeze a production made on an uncommitted tree — for a sample that is
                not committed; its header says so

        Returns:
            What was written
        """
        production = self._store.current().get(entry.entry_id)
        if production is None:
            raise GoldenSampleNoProductionError(entry.entry_id)
        runs = [self._frozen_run_of(run_id) for run_id in production.run_ids]
        # Unknown is not clean: a run that cannot say whether its tree was dirty cannot name its
        # code either (CLAUDE.md §50).
        unclean = [run.run_id for run in runs if run.dirty is not False]
        if unclean and not allow_dirty:
            raise GoldenSampleDirtyProductionError(entry.entry_id, unclean)

        all_rows = self._ledger.read_rows()
        all_runs = self._run_index.list_runs()
        wanted = set(production.run_ids)
        frozen: List[FrozenRun] = []
        for meta in runs:
            run = FrozenRun(run_id=meta.run_id,
                            ledger_rows=[row for row in all_rows if row.run_id == meta.run_id])
            if _freezes_sections(entry):
                for field_name, spec in FROZEN_SECTIONS:
                    setattr(run, field_name, self._run_entries.read_section(meta.run_id, spec))
            meta.sections = [spec.filename for field_name, spec in FROZEN_SECTIONS
                             if getattr(run, field_name) is not None]
            frozen.append(run)

        now = datetime.now(timezone.utc)
        header = GoldenSampleHeader(
            entry_id=entry.entry_id, produced_at=production.produced_at,
            report_contract=production.report_contract, frozen_at=now, composed_at=now,
            runs=runs, deployment_ids=production.deployment_ids, sweep_ids=production.sweep_ids)
        sample = GoldenSample(
            header=header, runs=frozen,
            inputs=GoldenSampleInputs(
                ledger_rows=[row for row in all_rows if row.run_id in wanted],
                run_index=[run for run in all_runs if run.run_id in wanted]),
            views=compose_views(all_rows, all_runs, now, production.run_ids,
                                production.deployment_ids, production.sweep_ids))

        # Checked on what the files will hold, not on what is in memory: a field that does not
        # survive the round trip is a field the suite would never see.
        with tempfile.TemporaryDirectory() as scratch:
            write_golden_sample(sample, Path(scratch))
            reread = load_golden_sample(Path(scratch), entry.entry_id)
        differences = check_golden_sample(reread)
        if differences:
            raise GoldenSampleNotReproducedError(entry.entry_id, differences)

        folder = write_golden_sample(sample, self._root)
        return GoldenSampleFreeze(
            folder=folder, header=header,
            block_checks=sum(1 for block in GOLDEN_SAMPLE_BLOCKS for run in reread.runs
                             if block_applies(block, run)),
            view_subjects=(len(sample.views.run_list_figures) + len(header.deployment_ids)
                           + len(header.sweep_ids)))

    def _frozen_run_of(self, run_id: str) -> GoldenSampleRun:
        """
        One run of the production, with the code its header names.

        Args:
            run_id: The run

        Returns:
            The run's identity and code; the code fields None when it holds no header
        """
        header = self._run_entries.read_header(run_id)
        if header is None:
            return GoldenSampleRun(run_id=run_id)
        framework = header.code_identity.framework if header.code_identity else None
        return GoldenSampleRun(
            run_id=run_id, run_type=header.run_type, started_at=header.start_time,
            commit=framework.commit if framework else None,
            branch=framework.branch if framework else None,
            dirty=framework.dirty if framework else None,
            diff_hash=framework.diff_hash if framework else None)


def _freezes_sections(entry: FixtureEntry) -> bool:
    """
    Whether an entry's runs freeze their report sections, or only their ledger rows.

    A sweep's combinations are ordinary backtests: their sections exercise the blocks the
    report-coverage backtest already does, at several megabytes of trade rows. What only a sweep
    has is its ranking, and that is composed from ledger rows.

    Args:
        entry: The catalog entry

    Returns:
        False for a sweep, True for every other producer
    """
    return entry.producer is not FixtureProducerKind.SWEEP
