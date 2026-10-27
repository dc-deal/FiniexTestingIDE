"""
The routes a run's files are served under (#621): every member that declares a route is served
there, and every route that serves one run's files belongs to a member — or to another store.
"""

from python.api.api_app import create_app
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.types.store_types import StoreId

_RUN_ROUTES = '/api/v1/reports/runs/{run_id}/'

# Routes below a run that serve something other than a file of its directory, and the store they
# serve it from.
_FROM_OTHER_STORES = {'config': StoreId.RUN_CONFIGS}


def _served_below_a_run() -> set:
    """The paths the API serves below one run, without the run's prefix."""
    return {path[len(_RUN_ROUTES):] for path in create_app().openapi()['paths']
            if path.startswith(_RUN_ROUTES)}


class TestTheDeclaredRoutesAreTheServedOnes:

    def test_every_declared_route_is_served(self):
        served = _served_below_a_run()
        for member in RUN_ENTRY_MEMBERS:
            if member.served_route is not None:
                assert member.served_route in served, (
                    f'{member.member_id} declares {member.served_route}, which no route serves')

    def test_every_route_below_a_run_serves_a_member_or_another_store(self):
        declared = {member.served_route for member in RUN_ENTRY_MEMBERS if member.served_route}
        unclaimed = _served_below_a_run() - declared - set(_FROM_OTHER_STORES)
        assert not unclaimed, (
            f'routes below a run that serve no declared member: {sorted(unclaimed)} — declare '
            f'served_route on the member, or name the other store here')

    def test_no_two_members_claim_one_route(self):
        routes = [member.served_route for member in RUN_ENTRY_MEMBERS if member.served_route]
        assert routes and len(routes) == len(set(routes))
