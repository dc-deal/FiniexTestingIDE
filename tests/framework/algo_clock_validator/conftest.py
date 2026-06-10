"""
Fixtures for the Algo Clock Validator suite (#359).

Code-level fixtures live here (§34): a recording logger and importlib loading
of the algo fixture files under tests/fixtures/algo_clock_validator/.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).resolve().parents[3] / 'tests' / 'fixtures' / 'algo_clock_validator'


def load_fixture_class(file_name: str, class_name: str) -> type:
    """
    Load a class from an algo fixture file via importlib (mirrors factory loading).

    The module is registered in sys.modules (exactly like the factories do) —
    without that, inspect.getsourcefile() cannot resolve the class's source file.

    Args:
        file_name: Fixture file name (e.g. 'wall_clock_logic.py')
        class_name: Class to pull from the loaded module

    Returns:
        The loaded class object
    """
    path = FIXTURES_DIR / file_name
    spec = importlib.util.spec_from_file_location(f'algo_clock_fixture.{path.stem}', str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, class_name)


class RecordingLogger:
    """Captures log calls so tests can assert on emitted warnings/infos."""

    def __init__(self):
        self.infos = []
        self.warnings = []
        self.errors = []

    def info(self, msg: str) -> None:
        self.infos.append(msg)

    def warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def debug(self, msg: str) -> None:
        pass


@pytest.fixture
def logger() -> RecordingLogger:
    return RecordingLogger()
