"""
Golden sample files (#557) — one folder per fixture catalog entry, written whole and read back.

An entry folder holds the sample's header, the report sections of each of its runs exactly as
the section writer writes them, the ledger and run index rows its read-time views are composed
from, and those views. A sample is replaced as a whole: written next to the old one, then swapped
in, so a folder never holds half of one production and half of another.
"""

import shutil
from pathlib import Path
from typing import List

from python.framework.constants.golden_sample_constants import (
    GOLDEN_SAMPLE_HEADER_FILE,
    GOLDEN_SAMPLE_INPUTS_FILE,
    GOLDEN_SAMPLE_RUNS_DIR,
    GOLDEN_SAMPLE_VIEWS_FILE,
)
from python.framework.exceptions.golden_sample_errors import GoldenSampleMissingError
from python.framework.fixture_catalog.golden_samples.golden_sample_checks import FROZEN_SECTIONS
from python.framework.reporting.io.report_artifact_io import read_artifact_in, write_artifact
from python.framework.types.golden_sample_types import (
    FrozenRun,
    GoldenSample,
    GoldenSampleHeader,
    GoldenSampleInputs,
    GoldenSampleViews,
)
from python.framework.utils.file_utils import create_directory


def write_golden_sample(sample: GoldenSample, root: Path) -> Path:
    """
    Write a golden sample into its entry folder, replacing the one there as a whole.

    Args:
        sample: The sample — its header names the entry
        root: The golden samples root

    Returns:
        The entry folder
    """
    entry_id = sample.header.entry_id
    folder = root / entry_id
    staging = root / f'.{entry_id}.staging'
    if staging.exists():
        shutil.rmtree(staging)
    create_directory(staging, holds=f'the golden sample of {entry_id} while it is written')
    (staging / GOLDEN_SAMPLE_HEADER_FILE).write_text(
        sample.header.model_dump_json(indent=1), encoding='utf-8')
    (staging / GOLDEN_SAMPLE_INPUTS_FILE).write_text(
        sample.inputs.model_dump_json(indent=1), encoding='utf-8')
    (staging / GOLDEN_SAMPLE_VIEWS_FILE).write_text(
        sample.views.model_dump_json(indent=1), encoding='utf-8')
    for run in sample.runs:
        run_folder = create_directory(
            staging / GOLDEN_SAMPLE_RUNS_DIR / run.run_id,
            holds=f'the frozen report sections of run {run.run_id}')
        for field_name, spec in FROZEN_SECTIONS:
            section = getattr(run, field_name)
            if section is not None:
                write_artifact(section, run_folder, spec)
    if folder.exists():
        shutil.rmtree(folder)
    staging.rename(folder)
    return folder


def load_golden_sample(root: Path, entry_id: str) -> GoldenSample:
    """
    Read one entry's golden sample back.

    Args:
        root: The golden samples root
        entry_id: The fixture catalog entry

    Returns:
        The sample, its runs in the header's order with their ledger rows attached
    """
    folder = root / entry_id
    if not (folder / GOLDEN_SAMPLE_HEADER_FILE).is_file():
        raise GoldenSampleMissingError(entry_id, str(folder))
    header = GoldenSampleHeader.model_validate_json(
        (folder / GOLDEN_SAMPLE_HEADER_FILE).read_bytes())
    inputs = GoldenSampleInputs.model_validate_json(
        (folder / GOLDEN_SAMPLE_INPUTS_FILE).read_bytes())
    views = GoldenSampleViews.model_validate_json(
        (folder / GOLDEN_SAMPLE_VIEWS_FILE).read_bytes())
    runs: List[FrozenRun] = []
    for frozen in header.runs:
        run_folder = folder / GOLDEN_SAMPLE_RUNS_DIR / frozen.run_id
        run = FrozenRun(run_id=frozen.run_id,
                        ledger_rows=[row for row in inputs.ledger_rows
                                     if row.run_id == frozen.run_id])
        for field_name, spec in FROZEN_SECTIONS:
            setattr(run, field_name, read_artifact_in(run_folder, spec))
        runs.append(run)
    return GoldenSample(header=header, runs=runs, inputs=inputs, views=views)


def frozen_entry_ids(root: Path) -> List[str]:
    """
    The entries a golden sample is frozen for.

    Args:
        root: The golden samples root

    Returns:
        Their ids, sorted; empty when nothing is frozen. A staging folder a broken write left
        behind is no entry
    """
    if not root.is_dir():
        return []
    return sorted(path.name for path in root.iterdir()
                  if path.is_dir() and not path.name.startswith('.')
                  and (path / GOLDEN_SAMPLE_HEADER_FILE).is_file())
