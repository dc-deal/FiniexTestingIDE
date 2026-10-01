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
      would be tempted to drop. The real case is `period_no`, which restarts per bot, so two
      periods of one deployment can both be number 2 and only `run_id` tells them apart.

A response serving ONE list declares `key`; one serving several declares `keys`, one entry per
list — a single `key` over two row types would name fields one of them does not have.
"""

from types import NoneType, UnionType
from typing import Union, get_args, get_origin

import pytest

from python.api.api_app import create_app
from python.framework.reporting.builders import deployment_history_builder
from python.framework.types.api.report_types import (
    BOOKING_PERIOD_KEY,
    DEPLOYMENT_KEY,
    SESSION_KEY,
    DeploymentBookingPeriodRow,
    DeploymentSessionRow,
    DeploymentSummary,
    PendingOrdersReport,
    PendingOrdersUnitRow,
    TradeHistoryReport,
    TradeHistoryRow,
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
            annotation = _without_none(field.annotation)
            if get_origin(annotation) is not list:
                continue
            (row_model,) = get_args(annotation) or (None,)
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


def _without_none(annotation):
    """
    The annotation an optional field wraps — `list[X] | None` is still a list of rows.

    Args:
        annotation: A field's annotation

    Returns:
        The single non-None member of an optional, else the annotation unchanged
    """
    if get_origin(annotation) in (Union, UnionType):
        members = [arg for arg in get_args(annotation) if arg is not NoneType]
        if len(members) == 1:
            return members[0]
    return annotation


def _declared_key(model, field: str):
    """
    The key a response declares for one of its lists.

    Args:
        model: The response model
        field: The list field

    Returns:
        The key parts, or None when the model declares none for that list
    """
    if 'keys' in model.model_fields:
        return model.model_fields['keys'].default.get(field)
    if 'key' in model.model_fields:
        return model.model_fields['key'].default
    return None


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
        declares_any = 'key' in model.model_fields or 'keys' in model.model_fields
        if _RUN_SCOPED in path and not declares_any:
            pytest.skip('run-scoped section: its identity is the run it was asked for')
        assert _declared_key(model, field) is not None, (
            f'{path} serves `{field}` and declares no key for it (§49)')

    def test_the_key_names_only_real_fields(self, path, model, field, row_model):
        declared = _declared_key(model, field)
        if declared is None:
            pytest.skip('covered by the case above')
        for part in declared:
            assert part in row_model.model_fields, (
                f'{model.__name__}.key names {part!r}, which is not a field of '
                f'{row_model.__name__} — a consumer keying on it reads nothing')


def test_a_response_with_several_lists_keys_each_one():
    """One `key` over two row types names fields one of them does not have — `keys` it is."""
    lists_per_model = {}
    for path, model, field, _ in LIST_RESPONSES:
        lists_per_model.setdefault((path, model), []).append(field)
    for (path, model), fields in lists_per_model.items():
        if len(fields) > 1 and 'key' in model.model_fields:
            pytest.fail(f'{path} serves {fields} under ONE `key` — declare `keys`, one per list')


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
        # The measured case (2026-09-22): `period_no` is a per-BOT counter carried through the
        # cold-start state, so two periods of ONE deployment are both number 2 and only the run
        # tells them apart. Without `run_id` a Gantt draws one bar where there were two.
        rows = [
            _period(run_id='20260922_231031_90cee58a', period_no=2),
            _period(run_id='20260922_231104_79574544', period_no=2),
        ]
        assert _distinct(rows, BOOKING_PERIOD_KEY) == 2
        assert _distinct(rows, ('unit_name', 'period_no')) == 1

    def test_the_declared_key_is_the_one_the_fold_groups_by(self):
        # SESSION_KEY is literally the `by=` of `build_deployment_histories`. Held here so a
        # change to the grouping cannot leave the declaration behind.
        assert deployment_history_builder.SESSION_KEY == SESSION_KEY

    def test_a_trade_needs_its_unit_and_its_closing_tick(self):
        # Both measured cases (2026-09-27): a partial close books several records of ONE
        # position, and two scenarios of one symbol each count from `pos_<symbol>_1`.
        key = TradeHistoryReport.model_fields['keys'].default['trades']
        rows = [
            _trade('blocks_06', 'pos_ethusd_1', exit_tick_index=410),
            _trade('blocks_09', 'pos_ethusd_1', exit_tick_index=410),     # another scenario
            _trade('blocks_09', 'pos_ethusd_1', exit_tick_index=977),     # a partial close
        ]
        assert _distinct(rows, key) == 3
        assert _distinct(rows, ('position_id',)) == 1                  # what it would collapse to

    def test_a_pending_orders_unit_is_its_name(self):
        # One unit per scenario, and scenario names are unique within a set — a repeat is
        # refused at validation (`scenario_name_duplicate`). The run-scoped walk above exempts
        # every report route, so this case is what holds the declaration (contract 19).
        key = PendingOrdersReport.model_fields['key'].default
        assert key == ['name']
        assert set(key) <= set(PendingOrdersUnitRow.model_fields)


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


def _trade(scenario_name: str, position_id: str, exit_tick_index: int) -> TradeHistoryRow:
    """
    One trade row, varying only in what the case under test needs.

    Args:
        scenario_name: The unit that booked it
        position_id: Its position
        exit_tick_index: The tick it closed on

    Returns:
        The row
    """
    return TradeHistoryRow(
        position_id=position_id, symbol='ETHUSD', direction='long', lots=0.1,
        entry_price=2000.0, entry_time='2026-01-25T20:20:02+00:00', exit_price=2010.0,
        exit_time='2026-01-26T01:21:39+00:00', duration_s=18097.0, close_reason='manual',
        gross_pnl=1.0, total_fees=0.1, net_pnl=0.9, currency='USD',
        scenario_name=scenario_name, exit_tick_index=exit_tick_index)


def _period(run_id: str, period_no: int) -> DeploymentBookingPeriodRow:
    """
    One booking-period row, varying only in what the case under test needs.

    Args:
        run_id: Which session booked it
        period_no: Its running number

    Returns:
        The row
    """
    return DeploymentBookingPeriodRow(
        run_id=run_id, unit_name='demo_btcusd_bot', period_no=period_no,
        opened_at='2026-01-24T14:19:46+00:00', closed_at='2026-01-25T00:00:00+00:00',
        reason='anchor', currency='USD', trade_count=0, net_pnl=0.0, total_fees=0.0,
        win_rate=0.0, profit_factor=None, final_equity=10_000.0, min_equity=10_000.0,
        max_equity=10_000.0, max_drawdown=0.0)
