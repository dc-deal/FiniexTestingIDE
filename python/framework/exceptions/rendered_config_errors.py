"""
FiniexTestingIDE - Rendered Config Errors (#547)

RenderedConfigMismatchError: the configuration a session recorded as what it RAN differs from the
parameters its decision logic and workers were actually built with. Both come from the same factory
helper, so a mismatch is a defect in our own code — and a record that misstates a real-money
session is worse than no record, so the session refuses to start.

RecordedScenarioRefusedError: a backtest of a session's window cannot be built from what the
session recorded (#512) — a value the backtest cannot reproduce, or a document that lacks a block.
"""

from python.framework.exceptions.finiex_error import FiniexError


class RenderedConfigMismatchError(FiniexError, RuntimeError):
    """The rendered configuration and the constructed components disagree."""

    def __init__(self, component: str, detail: str):
        self.component = component
        super().__init__(
            f'The rendered configuration of this session does not match what {component} was '
            f'built with ({detail}). The run would record a configuration it did not run, so it '
            f'does not start. Both sides come from the same factory helper — this is a defect, '
            f'not a setting.'
        )


class RecordedScenarioRefusedError(FiniexError, ValueError):
    """A backtest cannot be built from a session's recorded configuration as it stands."""
