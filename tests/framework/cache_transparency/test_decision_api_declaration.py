"""
The Decision API's Declaration — every public method says whether it reads or acts (#653).

A decision sees the run only through the READS methods of the decision trading API and the events
it subscribes to, so those are where a cache could reach it. The declaration names each method's
kind, and the observer probe calls every READS method on every pass. These tests hold the two to the
class in both directions, so a new method cannot arrive unobserved.
"""

import inspect

from python.framework.decision_logic.core.test_probes.observer_probe import (
    OBSERVED_DECISION_API_READS,
)
from python.framework.trading_env.decision_trading_api import (
    DECISION_API_METHOD_KINDS,
    DecisionTradingApi,
)
from python.framework.types.trading_env_types.decision_api_method_types import (
    DecisionApiMethodKind,
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
