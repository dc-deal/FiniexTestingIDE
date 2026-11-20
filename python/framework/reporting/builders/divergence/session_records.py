"""
Where the divergence report (#512) reads a live deployment's records: the configured stores of
this machine, or a copy of another machine's data home.

A live bot runs on its own host, and its records reach the machine that compares them as a copy
of that host's data home: the run index, the ledger, the run-config store and the run directories,
laid out as the host laid them out. The run index names each run's directory by the host's path,
so the copy's index is read once and written beside the comparison with every directory moved into
the copy — the record of what was read. Each store is found in the copy by its own index file.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.store.report_store import ReportStore
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.store.abstract_store_index import store_index_filename
from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.store_types import StoreId
from python.framework.utils.atomic_file_utils import write_file_atomic

# The run index's column naming each run's directory
_RUN_DIR_COLUMN = 'run_dir'


@dataclass
class SessionRecords:
    """
    The stores a deployment's records are read from.

    Args:
        index_path: The run index read
        run_index: That index
        reader: A run reader over it
        ledger: The run-results ledger
        report_store: The report store over the index and the ledger
        run_configs: The run-config store holding the sessions' frozen documents
        root: The copy's root; '' where the configured stores are read
    """
    index_path: Path
    run_index: RunIndex
    reader: RunEntryReader
    ledger: RunResultsLedger
    report_store: ReportStore
    run_configs: RunConfigStore
    root: str = ''


def configured_records() -> SessionRecords:
    """
    The records this machine's configured stores hold.

    Returns:
        The stores
    """
    config = AppConfigManager()
    index_path = Path(config.get_file_logging_config_object().run_index)
    ledger_dir = Path(config.get_run_ledger_path())
    return SessionRecords(
        index_path=index_path, run_index=RunIndex(index_path),
        reader=RunEntryReader(index_path), ledger=RunResultsLedger(ledger_dir),
        report_store=ReportStore(index_path, ledger_dir),
        run_configs=RunConfigStore(Path(config.get_run_configs_path())))


def copied_records(copy_root: Path, index_out: Path) -> SessionRecords:
    """
    The records a copy of another machine's data home holds.

    Each store is found in the copy by its index file, named as the store catalog names it — not by
    this machine's configured paths, which are this machine's own and may lie elsewhere.

    Args:
        copy_root: The copy's root — the other machine's data home, as copied
        index_out: Where the copy's run index is written with its directories moved into the copy

    Returns:
        The stores, read from the copy
    """
    copied_index = _only_index(copy_root, StoreId.RUNS)
    rows = pd.read_parquet(copied_index)
    rows[_RUN_DIR_COLUMN] = [str(_moved(Path(run_dir), copy_root))
                             for run_dir in rows[_RUN_DIR_COLUMN]]
    write_file_atomic(index_out, lambda path: rows.to_parquet(path))
    ledger_dir = _only_index(copy_root, StoreId.RUN_LEDGER).parent
    return SessionRecords(
        index_path=index_out, run_index=RunIndex(index_out), reader=RunEntryReader(index_out),
        ledger=RunResultsLedger(ledger_dir), report_store=ReportStore(index_out, ledger_dir),
        run_configs=RunConfigStore(_only_index(copy_root, StoreId.RUN_CONFIGS).parent),
        root=str(copy_root))


def _only_index(copy_root: Path, store_id: StoreId) -> Path:
    """
    The one index file of a store in a copy.

    Args:
        copy_root: The copy's root
        store_id: The store

    Returns:
        Its index file
    """
    found = sorted(copy_root.rglob(store_index_filename(store_id)))
    if len(found) != 1:
        raise FileNotFoundError(
            f'{copy_root} holds {len(found)} index file(s) of the store {store_id.value} '
            f'({store_index_filename(store_id)}); a copy of one data home holds exactly one.')
    return found[0]


def _moved(run_dir: Path, copy_root: Path) -> Path:
    """
    A run directory as the other machine named it, moved into the copy: the longest end of its
    path that lies in the copy.

    Args:
        run_dir: The directory the copy's index names
        copy_root: The copy's root

    Returns:
        The directory inside the copy
    """
    parts = run_dir.parts[1:] if run_dir.is_absolute() else run_dir.parts
    for start in range(len(parts)):
        candidate = copy_root.joinpath(*parts[start:])
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f'The copied run index names {run_dir}, and no end of that path lies '
                            f'in {copy_root}.')
