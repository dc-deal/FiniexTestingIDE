"""
The Decision API's Declaration — every public method says whether it reads or acts (#653), and
every read says what it hands the decision (#654).

A decision sees the run only through the READS methods of the decision trading API and the events
it subscribes to, so those are where a cache could reach it. The declaration names each method's
kind, and the observer probe calls every READS method on every pass. These tests hold the two to the
class in both directions, so a new method cannot arrive unobserved.
"""

import dataclasses
import inspect
import typing
from datetime import datetime, timedelta
from enum import Enum

from python.framework.decision_logic.core.test_probes.observer_probe import (
    OBSERVED_DECISION_API_READS,
)
from python.framework.trading_env.algo_masks.abstract_algo_mask import AbstractAlgoMask
from python.framework.trading_env.decision_trading_api import (
    DECISION_API_METHOD_KINDS,
    DECISION_API_READS_RETURN,
    DecisionTradingApi,
)
from python.framework.types.trading_env_types.decision_api_method_types import (
    DecisionApiMethodKind,
    DecisionApiReturnKind,
)


def _public_methods() -> set:
    """The public methods the decision trading API defines."""
    return {name for name, member in inspect.getmembers(DecisionTradingApi, inspect.isfunction)
            if not name.startswith('_')}


class TestTheDeclarationIsComplete:
    """Every public method is declared, and nothing is declared that the class lacks."""

    def test_every_public_method_is_declared(self):
        undeclared = _public_methods() - set(DECISION_API_METHOD_KINDS)
        assert not undeclared, f'declare whether these read or act: {sorted(undeclared)}'

    def test_nothing_is_declared_the_class_lacks(self):
        stale = set(DECISION_API_METHOD_KINDS) - _public_methods()
        assert not stale, f'declared, but not on the class: {sorted(stale)}'

    def test_both_kinds_occur(self):
        assert set(DECISION_API_METHOD_KINDS.values()) == set(DecisionApiMethodKind)


class TestTheProbeObservesEveryRead:
    """The observer probe calls exactly the READS methods, so a cache has nowhere to hide."""

    def test_the_probe_reads_what_the_api_declares(self):
        declared_reads = {name for name, kind in DECISION_API_METHOD_KINDS.items()
                          if kind is DecisionApiMethodKind.READS}
        assert OBSERVED_DECISION_API_READS == declared_reads, (
            f'not observed: {sorted(declared_reads - OBSERVED_DECISION_API_READS)}; '
            f'observed but not declared a read: {sorted(OBSERVED_DECISION_API_READS - declared_reads)}')


_PLAIN_VALUES = (bool, int, float, str, datetime, timedelta, type(None))


def _returned_type(name: str):
    """The type a READS method declares it returns, a list's or an optional's member unwrapped."""
    returned = typing.get_type_hints(getattr(DecisionTradingApi, name))['return']
    while typing.get_origin(returned) in (list, typing.Union):
        members = [arg for arg in typing.get_args(returned) if arg is not type(None)]
        returned = members[0] if len(members) == 1 else returned
        if len(members) != 1:
            break
    return returned


class TestEveryReadSaysWhatItReturns:
    """Each READS method declares what it hands the decision, and its return type agrees — so an
    engine object cannot reach an algo through a read nobody looked at."""

    def test_every_read_is_declared_and_nothing_else(self):
        declared_reads = {name for name, kind in DECISION_API_METHOD_KINDS.items()
                          if kind is DecisionApiMethodKind.READS}
        assert set(DECISION_API_READS_RETURN) == declared_reads

    def test_a_mask_read_returns_a_mask(self):
        for name, kind in DECISION_API_READS_RETURN.items():
            if kind is DecisionApiReturnKind.MASK:
                assert issubclass(_returned_type(name), AbstractAlgoMask), name

    def test_a_frozen_read_returns_a_frozen_value(self):
        for name, kind in DECISION_API_READS_RETURN.items():
            if kind is DecisionApiReturnKind.FROZEN:
                returned = _returned_type(name)
                assert dataclasses.is_dataclass(returned), name
                assert returned.__dataclass_params__.frozen, f'{name}: {returned.__name__} is not frozen'

    def test_a_value_read_returns_a_plain_value(self):
        for name, kind in DECISION_API_READS_RETURN.items():
            if kind is DecisionApiReturnKind.VALUE:
                returned = _returned_type(name)
                assert returned in _PLAIN_VALUES or (
                    isinstance(returned, type) and issubclass(returned, Enum)), name

    def test_a_copy_read_is_neither_a_mask_nor_frozen_nor_plain(self):
        """A copy is a mutable value made for the call: declaring one is a claim the reviewer checks
        in the method, so it must not be mistaken for any of the other kinds."""
        for name, kind in DECISION_API_READS_RETURN.items():
            if kind is DecisionApiReturnKind.COPY:
                returned = _returned_type(name)
                origin = typing.get_origin(returned) or returned
                assert origin is dict or (dataclasses.is_dataclass(returned)
                                          and not returned.__dataclass_params__.frozen), name

