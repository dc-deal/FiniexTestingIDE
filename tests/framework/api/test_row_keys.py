"""
Every list the API serves declares what makes one of its rows unique — and the declaration holds.

§49 asks for the declaration; this is the gate it asks for in the same breath, and it was
missing for three hours after the declaration shipped. Without it `key` is a comment that
decays: nothing notices a new list route that declares none, a name that is not a field of the
row it describes, or a key that no longer separates the rows the fold produces.

TWO halves, because they fail differently:

  A · COMPLETENESS — walked from the MOUNTED ROUTES, so a route added tomorrow is covered the
      moment it exists. A typo (`curency`) passes today and reaches a consumer as a field that
      is not there.

  B · SEPARATION — an ADVERSARIAL payload: rows that differ only in the part of the key one
      would be tempted to drop. The real case is `segment_no`, which restarts per bot, so two
      periods of one deployment can both be number 2 and only `run_id` tells them apart.
"""

from typing import get_args, get_origin

import pytest

from python.api.api_app import create_app
from python.framework.types.api.report_types import (
    BOOKING_PERIOD_KEY,
    DEPLOYMENT_KEY,
    SESSION_KEY,
    DeploymentBookingPeriodRow,
    DeploymentSessionRow,
    DeploymentSummary,
)

# A COLLECTION route serves rows a consumer iterates; a RUN-SCOPED report serves the sections of
# ONE run, where the identity is the run it was asked for and the inner list is a projection of
# it. Matched by path so exempting one stays a decision rather than a pattern.
_RUN_SCOPED = '/reports/runs/{run_id}/'


def _list_responses():
    """
    Every response model the APP actually serves that carries a list of rows.

    Walked from the mounted routes, not listed here and not swept over the types module. Two
    reasons. A list here would be the copy this section exists to prevent. And the types module
    holds plenty of models whose rows sit INSIDE a report — what a consumer keys on is what a
    ROUTE hands back.

    Returns:
        (route path, model, field name, row model) per list-bearing served response
    """
    found = []
    for route in _every_route(create_app()):
        model = getattr(route, 'response_model', None)
        fields = getattr(model, 'model_fields', None)
        if fields is None:
            continue
        for field_name, field in fields.items():
            if get_origin(field.annotation) is not list:
                continue
            (row_model,) = get_args(field.annotation) or (None,)
            if getattr(row_model, 'model_fields', None) is None:
                continue        # list[str] and friends are not rows
            found.append((route.path, model, field_name, row_model))
    return found


def _every_route(app):
    """
    Flatten the app's routes, descending into the included routers.

    A router mounted with `include_router` appears as one object in `app.routes` and carries
    its own routes inside it, so a flat read finds only the four declared on the app itself —
    and a gate that walks four routes while believing it walked thirty is the worst shape a
    gate can have.

    Args:
        app: The FastAPI application

    Returns:
        Every route, app-level and router-level
    """
    flat = []
    pending = list(app.routes)
    while pending:
        route = pending.pop()
        # `include_router` wraps the router rather than splicing its routes in, and the wrapper
        # keeps the real one on `original_router`. Both shapes are handled: a walk that reads
        # only `app.routes` sees the four routes declared on the app itself.
        nested = getattr(route, 'routes', None) or getattr(
            getattr(route, 'original_router', None), 'routes', None)
        if nested:
            pending.extend(nested)
            continue
        flat.append(route)
    return flat


LIST_RESPONSES = _list_responses()


def test_the_walk_found_the_served_lists():
    """A walk that came back empty would make every case below vacuous."""
    assert len(LIST_RESPONSES) > 5


@pytest.mark.parametrize('path,model,field,row_model', LIST_RESPONSES,
                         ids=[f'{path}:{field}' for path, _, field, _ in LIST_RESPONSES])
class TestEveryServedListSaysWhatMakesARowUnique:
    """
    Half A. A route's answer IS a contract, and an unordered list of objects says nothing about
    its own identity — so a consumer keying on the obvious field folds two rows into one.
    """

    def test_it_declares_a_key(self, path, model, field, row_model):
        if _RUN_SCOPED in path:
            pytest.skip('run-scoped section: its identity is the run it was asked for')
        assert 'key' in model.model_fields, (
            f'{path} serves `{field}` and declares no key (§49)')

    def test_the_key_names_only_real_fields(self, path, model, field, row_model):
        declared = model.model_fields.get('key')
        if declared is None:
            pytest.skip('covered by the case above')
        for part in declared.default:
            assert part in row_model.model_fields, (
                f'{model.__name__}.key names {part!r}, which is not a field of '
                f'{row_model.__name__} — a consumer keying on it reads nothing')


class TestTheKeyActuallySeparatesTheRows:
    """
    Half B. A key that names real fields can still fail to separate — and that failure is the
    expensive one: two rows collapse into one, silently, in the direction that loses data.
    """

    def test_a_deployment_row_needs_its_currency(self):
        # One deployment, two account currencies. Keyed on `deployment_id` alone these are ONE
        # row, and the P&L a consumer then shows is two currencies added together.
        rows = [
            DeploymentSummary(deployment_id='deploy_1', sessions=2, first_started=None,
                              last_started=None, net_pnl=10.0, max_drawdown=1.0,
                              max_drawdown_pct=0.1, currency='USD'),
            DeploymentSummary(deployment_id='deploy_1', sessions=2, first_started=None,
                              last_started=None, net_pnl=0.5, max_drawdown=0.01,
                              max_drawdown_pct=0.1, currency='BTC'),
        ]
        assert _distinct(rows, DEPLOYMENT_KEY) == 2
        assert _distinct(rows, ('deployment_id',)) == 1      # what it would collapse to

    def test_a_session_row_needs_its_currency_too(self):
        rows = [
            DeploymentSessionRow(index=1, run_id='r1', started=None, net_pnl=1.0,
                                 max_drawdown=0.0, max_drawdown_pct=0.0, currency='USD'),
            DeploymentSessionRow(index=1, run_id='r1', started=None, net_pnl=0.1,
                                 max_drawdown=0.0, max_drawdown_pct=0.0, currency='BTC'),
        ]
        assert _distinct(rows, SESSION_KEY) == 2
        assert _distinct(rows, ('run_id',)) == 1

    def test_a_booking_period_needs_its_run(self):
        # The measured case (2026-09-22): `segment_no` is a per-BOT counter carried through the
        # cold-start state, so two periods of ONE deployment are both number 2 and only the run
        # tells them apart. Without `run_id` a Gantt draws one bar where there were two.
        rows = [
            _period(run_id='20260922_231031_90cee58a', segment_no=2),
            _period(run_id='20260922_231104_79574544', segment_no=2),
        ]
        assert _distinct(rows, BOOKING_PERIOD_KEY) == 2
        assert _distinct(rows, ('unit_name', 'segment_no')) == 1

    def test_the_declared_key_is_the_one_the_fold_groups_by(self):
        # SESSION_KEY is literally the `by=` of `build_deployment_histories`. Held here so a
        # change to the grouping cannot leave the declaration behind.
        from python.framework.reporting.builders import deployment_history_builder
        assert deployment_history_builder.SESSION_KEY == SESSION_KEY


def _distinct(rows, key) -> int:
    """
    How many rows the key actually separates.

    Args:
        rows: The row models
        key: The key parts

    Returns:
        The number of distinct key tuples
    """
    return len({tuple(getattr(row, part) for part in key) for row in rows})


def _period(run_id: str, segment_no: int) -> DeploymentBookingPeriodRow:
    """
    One booking-period row, varying only in what the case under test needs.

    Args:
        run_id: Which session booked it
        segment_no: Its running number

    Returns:
        The row
    """
    return DeploymentBookingPeriodRow(
        run_id=run_id, unit_name='demo_btcusd_bot', segment_no=segment_no,
        opened_at='2026-01-24T14:19:46+00:00', closed_at='2026-01-25T00:00:00+00:00',
        reason='anchor', currency='USD', trade_count=0, net_pnl=0.0, total_fees=0.0,
        win_rate=0.0, profit_factor=None, final_equity=10_000.0, min_equity=10_000.0,
        max_equity=10_000.0, max_drawdown=0.0)
