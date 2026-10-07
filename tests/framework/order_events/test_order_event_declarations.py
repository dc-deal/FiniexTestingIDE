"""
FiniexTestingIDE - Order-Event Declarations (#362)

The declared maps answer what the stream can contain: which event each ENDING status is recorded as, and
which pipeline writes each event type. Both are held to their subjects in both directions — a
status with no answer, a member nobody emits, and an emitter nobody declared all fail here.

The emitters are found in the source, as the validation check catalog finds its check ids: a
member is emitted where the executor names it, or where it books a status the first map turns
into it. The shared executor counts for both pipelines; a pipeline's own files must not name a
member that is not declared for it.
"""

import re
from pathlib import Path
from typing import Dict, FrozenSet, Set

import pytest

from python.framework.types.trading_env_types.executor_mode_types import ExecutorMode
from python.framework.types.trading_env_types.order_event_types import (
    ORDER_EVENT_BY_STATUS,
    ORDER_EVENT_PIPELINES,
    OrderEventType,
)
from python.framework.types.trading_env_types.order_types import OrderStatus

_TRADING_ENV = Path('python/framework/trading_env')
_SHARED = (_TRADING_ENV / 'abstract_trade_executor.py',)
_OWN: Dict[ExecutorMode, tuple] = {
    ExecutorMode.SIMULATION: (
        _TRADING_ENV / 'simulation' / 'trade_simulator.py',
        _TRADING_ENV / 'simulation' / 'order_latency_simulator.py',
    ),
    ExecutorMode.LIVE: (
        _TRADING_ENV / 'live' / 'live_trade_executor.py',
        _TRADING_ENV / 'live' / 'live_request_processor.py',
    ),
}


# An emission SITE, not a mention: a member handed to the recorder, or passed on as the event a
# fill is recorded as. A comparison or a comment names a member without emitting it.
_EVENT_SITE = re.compile(
    r'(?:_record_order_event\(\s*|\b(?:fill_event|event_type)=)OrderEventType\.([A-Z_]+)')
# A booked status: the status an ending row is built with. Not BrokerOrderStatus — the venue's
# vocabulary, which books nothing by itself.
_STATUS_SITE = re.compile(
    r'(?:\bstatus=|_ending_for_pending\(\s*\w+\s*,\s*)OrderStatus\.([A-Z_]+)')


def _named_members(paths: tuple) -> Set[OrderEventType]:
    """
    The event types these files emit — directly, or through a status they book.

    Args:
        paths: Source files

    Returns:
        The members found
    """
    text = '\n'.join(path.read_text(encoding='utf-8') for path in paths)
    named = {OrderEventType[name] for name in _EVENT_SITE.findall(text)}
    for name in _STATUS_SITE.findall(text):
        mapped = ORDER_EVENT_BY_STATUS.get(OrderStatus[name])
        if mapped is not None:
            named.add(mapped)
    return named


def _emitted_by(mode: ExecutorMode) -> Set[OrderEventType]:
    """
    Every member one pipeline's sources can write, the shared executor included.

    Args:
        mode: The pipeline

    Returns:
        The members found
    """
    return _named_members(_SHARED + _OWN[mode])


class TestTheStatusMapIsComplete:
    """Every status either ends an order as one event, or is declared to end none."""

    def test_every_status_has_an_answer(self):
        assert set(ORDER_EVENT_BY_STATUS) == set(OrderStatus)

    def test_only_the_submission_row_ends_nothing(self):
        assert [s for s, event in ORDER_EVENT_BY_STATUS.items() if event is None] == [
            OrderStatus.PENDING]

    def test_every_ending_is_its_own_event(self):
        endings = [e for e in ORDER_EVENT_BY_STATUS.values() if e is not None]
        assert len(endings) == len(set(endings)), 'two statuses would read as one ending'


class TestThePipelineMapIsComplete:
    """Every member says who writes it, and every writer is declared."""

    def test_every_member_is_declared(self):
        assert set(ORDER_EVENT_PIPELINES) == set(OrderEventType)

    def test_every_member_has_a_pipeline(self):
        empty = [m for m, modes in ORDER_EVENT_PIPELINES.items() if not modes]
        assert not empty, f'a member nothing writes satisfies every other check: {empty}'

    @pytest.mark.parametrize('mode', list(ExecutorMode), ids=lambda m: m.value)
    def test_every_declared_member_is_emitted(self, mode):
        declared: Set[OrderEventType] = {
            m for m, modes in ORDER_EVENT_PIPELINES.items() if mode in modes}
        missing = declared - _emitted_by(mode)
        assert not missing, f'declared for {mode.value}, emitted nowhere: {sorted(m.value for m in missing)}'

    @pytest.mark.parametrize('mode', list(ExecutorMode), ids=lambda m: m.value)
    def test_a_pipeline_emits_nothing_undeclared(self, mode):
        own = _named_members(_OWN[mode])
        allowed: FrozenSet[OrderEventType] = frozenset(
            m for m, modes in ORDER_EVENT_PIPELINES.items() if mode in modes)
        undeclared = own - allowed
        assert not undeclared, (
            f'{mode.value} writes members declared for another pipeline only: '
            f'{sorted(m.value for m in undeclared)}')
