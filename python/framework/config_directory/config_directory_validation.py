"""
FiniexTestingIDE - Config Directory Validation (#554)

The deep check the directory deliberately does not make on every read: every scenario set it
lists, run through the REAL loader — cascade, structural guard and Pydantic validation — the way
a run would load it. It is an explicit command rather than part of the listing because the
loader creates directories and logs, which a listing must never do.

Parameter-schema validation is still not part of it: that runs at the start of a batch, against
the resolved components, and is where a stale parameter name surfaces.
"""

from typing import List, Optional

from python.framework.config_directory.config_directory import ConfigDirectory
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigReadStatus,
    ConfigValidationResult,
)
from python.scenario.scenario_config_loader import ScenarioConfigLoader


def validate_scenario_sets(files: Optional[List[str]] = None) -> List[ConfigValidationResult]:
    """
    Run the loader over the directory's scenario sets.

    Args:
        files: Only these file names; None validates every scenario set the directory lists

    Returns:
        One result per file, in the directory's order; an unreadable file is a failed result
        with its read error, never an exception
    """
    directory = ConfigDirectory()
    rows = [row for row in directory.list_configs(refresh=True).rows
            if row.kind in (ConfigKind.SCENARIO_SET, None)
            and (files is None or row.file in files)]
    loader = ScenarioConfigLoader()
    results = []
    for row in rows:
        if row.status == ConfigReadStatus.UNREADABLE:
            results.append(ConfigValidationResult(file=row.file, ok=False, reason=row.reason))
            continue
        try:
            loaded = loader.load_config(str(directory.path_of(row.file)))
        except (ValueError, KeyError, TypeError, OSError) as error:
            # A configuration the loader refuses is the finding (§33); anything else is a defect
            # in the loader and propagates.
            results.append(ConfigValidationResult(file=row.file, ok=False, reason=str(error)))
            continue
        results.append(ConfigValidationResult(
            file=row.file, ok=True, scenarios=len(loaded.scenarios)))
    return results
