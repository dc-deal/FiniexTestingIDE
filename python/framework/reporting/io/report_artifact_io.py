"""
Report artifact IO (#486) — one write path and one read path for every report artifact.

Every report artifact is the same operation: serialize a Pydantic model to JSON in the run's
io/ directory, and validate it back. That operation used to be written out once per artifact —
eighteen near-identical units whose only real content was a file name and a model class.

The spec is generic so the collapse costs no typing: `read_artifact(path, BROKER_ARTIFACT)` is
statically a `BrokerReport`, exactly as the hand-written reader was. What the specs bind lives
in `artifact_specs.py`; the artifacts with real logic of their own (a CSV surface, a row filter)
keep it in `report_csv_io.py` and `report_filters.py`.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Generic, Type, TypeVar

from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


@dataclass(frozen=True)
class ArtifactSpec(Generic[T]):
    """
    One report artifact: what it is called, and what it decodes to.

    Args:
        filename: Its canonical name inside a run's io/ directory
        model: The Pydantic model it serializes from and validates back into
    """
    filename: str
    model: Type[T]


def write_artifact(report: T, run_dir: Path, spec: ArtifactSpec[T]) -> Path:
    """
    Persist a report as JSON in the run directory (the API's source).

    Args:
        report: The built report
        run_dir: The run's io/ directory
        spec: Which artifact this is

    Returns:
        Path of the written artifact
    """
    path = Path(run_dir) / spec.filename
    path.write_text(report.model_dump_json(indent=2), encoding='utf-8')
    return path


def read_artifact(path: Path, spec: ArtifactSpec[T]) -> T:
    """
    Read a persisted report artifact back into its model.

    Args:
        path: The artifact file
        spec: Which artifact this is — its model is what the bytes are validated against

    Returns:
        The decoded report
    """
    return spec.model.model_validate_json(Path(path).read_bytes())
