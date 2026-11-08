"""
Golden sample constants (#557) — where the frozen samples live and what each entry folder holds.

Shared by the tool that freezes the samples, the reader the suite loads them with and the store
catalog, so every one of them names the same files.
"""

from pathlib import Path

# The committed root, relative to the project root: one folder per fixture catalog entry.
GOLDEN_SAMPLES_DIR = Path('tests/fixtures/golden_samples')

# Inside an entry folder: where the sample comes from, the ledger rows and run index rows its
# read-time views are composed from, those views, and one folder per run with its report sections.
# Not `runs`: the repository ignores every folder of that name, the run tree's, so the sections
# under it would never be committed.
GOLDEN_SAMPLE_HEADER_FILE = 'sample.json'
GOLDEN_SAMPLE_INPUTS_FILE = 'inputs.json'
GOLDEN_SAMPLE_VIEWS_FILE = 'views.json'
GOLDEN_SAMPLE_RUNS_DIR = 'run_sections'
