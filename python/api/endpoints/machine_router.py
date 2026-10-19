"""
The machine this server runs on (#595, #603): what it has and uses, and what runs on it.

Both routes sit on the `machine` grant surface. Without a grant a token reaches only what the
application says about itself, and a machine's load and the work on it say what this installation
does — a row of the activity list names scenario sets, profiles, run ids and command lines.

The readers are built once per application and kept on `app.state`, so the processor share
`/machine-resources` serves measures over the window since the previous reading by any caller,
and `/activities` reads the memory alone and leaves that window as it was.
"""

from fastapi import APIRouter, Request

from python.api.api_error_catalog import ACTIVITY_REGISTRY_UNREADABLE, api_error
from python.api.api_route_documents import describes
from python.api.served_activity_list import served_activity_list
from python.api.served_machine_resources import describe_machine
from python.framework.activities.activity_snapshot import take_activity_snapshot
from python.framework.types.api.api_types import ActivityListResponse, MachineResourcesResponse
from python.framework.utils.git_info_utils import get_framework_root

router = APIRouter()


@router.get('/machine-resources', response_model=MachineResourcesResponse,
            openapi_extra=describes('server'))
def machine_resources(request: Request) -> MachineResourcesResponse:
    state = request.app.state
    return describe_machine(state.machine_reader.read(), state.machine_thresholds)


@router.get('/activities', response_model=ActivityListResponse,
            openapi_extra=describes('activities'))
def activities(request: Request) -> ActivityListResponse:
    state = request.app.state
    try:
        snapshot = take_activity_snapshot(state.activity_reader, state.machine_reader)
    except OSError as error:
        # The reason without the file name: no path of the machine leaves through the API
        raise api_error(ACTIVITY_REGISTRY_UNREADABLE,
                        reason=error.strerror or type(error).__name__)
    return served_activity_list(snapshot, state.machine_thresholds, get_framework_root())
