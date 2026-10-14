"""
Import Config Mirror Tests (#175)

The import screen's settings and the timings path are declared in the model or the manager
AND in configs/import_config.json, with the same values — a default nobody can see in the
file cannot be tuned.
"""

import json
from pathlib import Path

from python.configuration.import_config_manager import DEFAULT_IMPORT_TIMINGS_PATH
from python.framework.types.config_types.import_config_types import ImportDisplayConfig

IMPORT_CONFIG = Path(__file__).resolve().parents[3] / 'configs' / 'import_config.json'


def _raw() -> dict:
    return json.loads(IMPORT_CONFIG.read_text(encoding='utf-8'))


def test_display_defaults_are_mirrored_in_the_config_file():
    block = {key: value for key, value in _raw()['display'].items()
             if not key.startswith('_')}
    assert block == ImportDisplayConfig().model_dump()


def test_timings_path_default_is_mirrored_in_the_config_file():
    assert _raw()['paths']['import_timings'] == DEFAULT_IMPORT_TIMINGS_PATH
