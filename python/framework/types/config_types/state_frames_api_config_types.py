"""
FiniexTestingIDE - State Frames API Config Types (#611)

How the check-in on a running session judges whether it is still recording: the age of its latest
state frame against a number of its cadences.
"""

from pydantic import Field

from python.framework.types.config_types.strict_config_model import StrictConfigModel


class StateFramesApiConfig(StrictConfigModel):
    """
    When the latest-frame route reads a session's recording as stopped.

    Args:
        stopped_after_cadences: How many of its cadences the latest frame of a session reading the
            venue may age before the recording counts as stopped. At the default cadence of a
            minute the default of three lies above the liveness ping's stall of two minutes, so a
            stall the session can report itself, it reports first
    """

    stopped_after_cadences: int = Field(default=3, ge=1)
