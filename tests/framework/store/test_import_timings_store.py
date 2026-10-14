"""
Import Timings Store Tests (#175)

The measured step rates survive between imports as a moving average, a change of unit or
worker count starts the average again, and an unusable file only costs the estimate.
"""

import json

from python.framework.store.import_timings_store import SCHEMA_VERSION, ImportTimingsStore
from python.framework.types.import_types.import_progress_types import (
    ImportRateUnit,
    ImportStepId,
    ImportStepRate,
)


def test_missing_file_has_no_rates(tmp_path):
    assert ImportTimingsStore(tmp_path / 'import_timings.json').load() == {}


def test_record_then_load_round_trips(tmp_path):
    path = tmp_path / 'runtime' / 'import_timings.json'
    store = ImportTimingsStore(path)
    store.record({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.25)})

    rates = store.load()
    assert rates[ImportStepId.TICKS_IMPORT].seconds_per_unit == 0.25
    assert rates[ImportStepId.TICKS_IMPORT].samples == 1
    assert not list(path.parent.glob('*.tmp'))


def test_second_measurement_is_averaged(tmp_path):
    store = ImportTimingsStore(tmp_path / 'import_timings.json')
    store.record({ImportStepId.TICKS_INDEX: ImportStepRate(ImportRateUnit.FILE, 0.02)})
    store.record({ImportStepId.TICKS_INDEX: ImportStepRate(ImportRateUnit.FILE, 0.04)})

    rate = store.load()[ImportStepId.TICKS_INDEX]
    assert abs(rate.seconds_per_unit - 0.03) < 1e-9
    assert rate.samples == 2


def test_a_new_worker_count_starts_again(tmp_path):
    store = ImportTimingsStore(tmp_path / 'import_timings.json')
    store.record({ImportStepId.BARS_RENDER:
                  ImportStepRate(ImportRateUnit.MILLION_TICKS, 2.0, workers=2)})
    store.record({ImportStepId.BARS_RENDER:
                  ImportStepRate(ImportRateUnit.MILLION_TICKS, 1.0, workers=4)})

    rate = store.load()[ImportStepId.BARS_RENDER]
    assert (rate.seconds_per_unit, rate.samples, rate.workers) == (1.0, 1, 4)


def test_a_new_unit_starts_again(tmp_path):
    store = ImportTimingsStore(tmp_path / 'import_timings.json')
    store.record({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.FILE, 2.0)})
    store.record({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.1)})

    rate = store.load()[ImportStepId.TICKS_IMPORT]
    assert (rate.unit, rate.seconds_per_unit, rate.samples) == (ImportRateUnit.MB, 0.1, 1)


def test_the_file_keeps_the_usual_mode(tmp_path):
    path = tmp_path / 'import_timings.json'
    ImportTimingsStore(path).record(
        {ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.25)})
    # Written through a temporary file, which is owner-only; the result must not be
    reference = tmp_path / 'reference.json'
    reference.write_text('{}')
    assert path.stat().st_mode & 0o777 == reference.stat().st_mode & 0o777


def test_a_step_not_measured_again_keeps_its_rate(tmp_path):
    store = ImportTimingsStore(tmp_path / 'import_timings.json')
    store.record({ImportStepId.CACHES: ImportStepRate(ImportRateUnit.BAR_FILE, 1.4)})
    store.record({ImportStepId.TICKS_IMPORT: ImportStepRate(ImportRateUnit.MB, 0.25)})
    assert set(store.load()) == {ImportStepId.CACHES, ImportStepId.TICKS_IMPORT}


def test_another_schema_or_garbage_yields_no_rates(tmp_path):
    path = tmp_path / 'import_timings.json'
    path.write_text(json.dumps({'schema_version': SCHEMA_VERSION + 1, 'rates': {}}))
    assert ImportTimingsStore(path).load() == {}
    path.write_text('not json')
    assert ImportTimingsStore(path).load() == {}
