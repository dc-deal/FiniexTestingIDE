"""
FiniexTestingIDE - Suite Runner Configuration Types
Pydantic model for configs/test_config.json.
"""

from pydantic import Field

from python.framework.types.config_types.strict_config_model import StrictConfigModel


class SuiteRunnerConfig(StrictConfigModel):
    """
    How the unified test runner picks its suites and how many it runs at once.

    The runner used to read this file as a raw mapping and carry each default in its own getter,
    which made the getter the only place a default existed. The model owns them now; the file
    mirrors them, and the loader transfers the section without deciding anything.
    """

    excluded: list[str] = []
    ignored: list[str] = []
    fail_fast: bool = True

    lanes: int = Field(default=4, ge=1)
    """
    The ceiling on suites running side by side.

    It is a ceiling, not a count: the runner also asks the machine what it has free and starts the
    smaller of the two, so a second suite running beside this one on the same machine lowers the
    effective number by itself. Four leaves room for the other development lines on a 16-processor
    machine; a larger machine raises it through the user override rather than through code. The
    whole suite is single-core in practice, so the limit here is politeness toward the machine's
    other users, not its capacity.
    """

    suite_timeout_seconds: int = Field(default=900, ge=1)
    """
    How long one suite may run before the runner gives up on it.

    Running one suite at a time made a hanging suite obvious — nothing moved. Several at once hide
    it: the others finish and the runner waits with nothing to show. The value is generous against
    the longest suite measured (about four minutes), so it catches a hang rather than a slow run.
    """
