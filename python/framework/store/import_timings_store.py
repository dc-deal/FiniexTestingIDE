"""
FiniexTestingIDE - Import Timings Store
The measured speed of each import step, kept between imports to estimate the next one (#175).

One JSON file, written once at the end of a complete import into the production archive and
read at the start of the next. DERIVED and deletable: without the file the screen shows no
estimate, and the next complete import writes it again. Each rate is a moving average, so one
slow run on a busy machine moves the estimate without taking it over.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.import_types.import_progress_types import (
    ImportRateUnit,
    ImportStepId,
    ImportStepRate,
)
from python.framework.utils.atomic_file_utils import write_text_atomic

vLog = get_global_logger()

SCHEMA_VERSION = 1
# Weight of the newest measurement in the moving average.
SMOOTHING = 0.5


class ImportTimingsStore:
    """
    Reads and writes the timings file.

    Args:
        path: The timings file
    """

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> Dict[ImportStepId, ImportStepRate]:
        """
        The stored rate of every step that has one.

        A missing file is the ordinary first state and yields no rates; an unreadable one is
        reported and yields none either — the estimate is a convenience, never a reason to stop.

        Returns:
            Rates by step; empty when there is no usable file
        """
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding='utf-8'))
            if raw.get('schema_version') != SCHEMA_VERSION:
                vLog.warning(
                    f'⚠️  {self._path} has schema version {raw.get("schema_version")}, '
                    f'expected {SCHEMA_VERSION} — no estimate this time')
                return {}
            rates: Dict[ImportStepId, ImportStepRate] = {}
            for step_value, entry in raw.get('rates', {}).items():
                rates[ImportStepId(step_value)] = ImportStepRate(
                    unit=ImportRateUnit(entry['unit']),
                    seconds_per_unit=float(entry['seconds_per_unit']),
                    samples=int(entry.get('samples', 1)),
                    workers=entry.get('workers'),
                )
            return rates
        except (ValueError, KeyError, TypeError) as e:
            vLog.warning(f'⚠️  {self._path} could not be read ({e}) — no estimate this time')
            return {}

    def record(self, measured: Dict[ImportStepId, ImportStepRate]) -> None:
        """
        Fold one import's measured rates into the stored ones and write the file.

        A step whose unit or worker count changed starts again from the new measurement:
        averaging two different quantities would describe neither.

        Args:
            measured: The rates this import measured, one sample each

        Returns:
            None
        """
        merged = self.load()
        for step_id, rate in measured.items():
            previous = merged.get(step_id)
            if (previous is None or previous.unit != rate.unit
                    or previous.workers != rate.workers):
                merged[step_id] = rate
                continue
            merged[step_id] = ImportStepRate(
                unit=rate.unit,
                seconds_per_unit=(SMOOTHING * rate.seconds_per_unit
                                  + (1 - SMOOTHING) * previous.seconds_per_unit),
                samples=previous.samples + 1,
                workers=rate.workers,
            )

        document = {
            'schema_version': SCHEMA_VERSION,
            # Provenance only — nothing decides on it.
            'updated_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'rates': {
                step_id.value: {
                    'unit': rate.unit.value,
                    'seconds_per_unit': round(rate.seconds_per_unit, 6),
                    'samples': rate.samples,
                    **({'workers': rate.workers} if rate.workers is not None else {}),
                }
                for step_id, rate in sorted(merged.items(), key=lambda item: item[0].value)
            },
        }
        write_text_atomic(self._path, json.dumps(document, indent=2) + '\n')
