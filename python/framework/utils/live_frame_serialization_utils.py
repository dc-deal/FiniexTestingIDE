"""
FiniexTestingIDE - Live Frame Serialization
Thin PRESENT-stage encoder turning a live-telemetry frame into a JSON-safe dict.

The frames stay @dataclass runtime domain types (§6); JSON is a render concern.
This encoder is applied only where a JSON surface consumes the frame — the future
viewer push transport (#379/#380). No transport is implemented here.
"""

from dataclasses import asdict
from typing import Any, Dict, Union

from python.framework.types.autotrader_types.autotrader_display_types import AutoTraderDisplayStats
from python.framework.types.live_types.live_scenario_stats_types import LiveScenarioStats, LiveStatusFrame
from python.framework.utils.process_serialization_utils import serialize_value


def frame_to_json(
    frame: Union[LiveScenarioStats, AutoTraderDisplayStats, LiveStatusFrame]
) -> Dict[str, Any]:
    """
    Encode a live-telemetry frame as a JSON-safe dict.

    asdict() recurses the dataclass tree into plain dicts / lists; serialize_value
    then converts the leaves (datetime -> isoformat, Enum -> value), leaving the
    frame itself an untouched runtime object.

    Args:
        frame: A live-telemetry frame (sim progress / status or live session)

    Returns:
        JSON-serializable dict mirroring the frame
    """
    return serialize_value(asdict(frame))
