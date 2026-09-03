"""
FiniexTestingIDE - Decision Logic Hook Validation (#434 / #436 / #493)

Some questions the framework cannot answer for a strategy: what to do when the market goes
blind, when a sentiment feed goes stale, when the venue turns out to be holding an order from
a previous session. It can only enforce that the question IS answered — and it does that at
startup, in both pipelines, so a violation surfaces in a backtest rather than in week two of a
live run.

The checks live here rather than inside the orchestrator because they are validation (§14) and
because they are called from three different seams: two need the built instance and its
workers, one runs at the class level where the pipelines already resolve the declared order
types. Each returns a MESSAGE or None; raising stays with the caller, so the market-data check
can abort immediately while the signal check joins the orchestrator's collected errors — the
behaviour both had before they moved here.
"""

import inspect
from typing import Any, List, Optional, Type

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.trading_env_types.order_types import OrderType

# Order types a venue can HOLD across a restart. A bot that declares one of these can find its
# own order resting at boot; a MARKET-only bot cannot.
RESTING_ORDER_TYPES = (OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT)


def check_market_data_staleness_hook(decision_logic: Any) -> Optional[str]:
    """
    Every decision logic must program its reaction to the tick stream going blind (#436).

    Session-level, so it applies to a logic with no worker requirements at all. Sim never
    dispatches it (a replay gap is data, unless a stale_data_stress window drives it); the
    override is the uniform authoring contract — sim-validated means live-ready.

    Args:
        decision_logic: The built decision logic instance

    Returns:
        The failure message, or None when the hook is overridden
    """
    if _overrides(type(decision_logic), 'on_market_data_stale'):
        return None

    return (
        f"DecisionLogic '{decision_logic.__class__.__name__}' does not "
        f'override on_market_data_stale() — the market-outage reaction '
        f'(flat / wait-with-timeout / entries-block / deliberate pass) must '
        f'be programmed explicitly. See '
        f'docs/user_guides/live_outage_handling_guide.md.'
    )


def check_signal_staleness_hook(decision_logic: Any, consumes_signal: bool) -> Optional[str]:
    """
    A logic that consumes a SIGNAL worker must program its outage reaction (#434).

    Args:
        decision_logic: The built decision logic instance
        consumes_signal: Whether any required worker is a SIGNAL worker

    Returns:
        The failure message, or None when nothing is required or the hook is overridden
    """
    if not consumes_signal or _overrides(type(decision_logic), 'on_signal_stale'):
        return None

    return (
        'Decision logic consumes SIGNAL worker(s) but does not override '
        'on_signal_stale() — the staleness reaction (fallback / flat / HALT / '
        'deliberate ignore) must be programmed explicitly.'
    )


def check_cold_start_hook(
    decision_logic_class: Type[Any],
    required_order_types: List[OrderType],
) -> Optional[str]:
    """
    A logic that declares a RESTING order type must answer for the boot situation (#493).

    The condition is the declaration, not the pipeline: an order that can rest at a venue can
    still be there after a restart, and a bot that does not react to its own open order must
    not trade beside it. A MARKET-only logic is not asked — there is nothing it could find.

    Shape only: the hook must exist and be CALLABLE with the situation. What it ANSWERS is a
    strategy decision and is never validated, and neither are the annotations — an unannotated
    USER hook is legal Python and works fine. The check is a trial bind rather than a
    parameter count, because counting rejects shapes that work: an extra parameter with a
    default, a `*args` tail, or simply a first parameter not named `self`.

    Args:
        decision_logic_class: The resolved decision-logic CLASS (no instance needed)
        required_order_types: What it declared via get_required_order_types()

    Returns:
        The failure message, or None when nothing is required or the hook is sound
    """
    if not any(t in RESTING_ORDER_TYPES for t in required_order_types):
        return None

    if not _overrides(decision_logic_class, 'on_cold_start'):
        resting = [t.value for t in required_order_types if t in RESTING_ORDER_TYPES]
        return (
            f"DecisionLogic '{decision_logic_class.__name__}' declares resting order "
            f'type(s) {resting} but does not override on_cold_start() — an order that '
            f'rests at a venue can still be there after a restart, and what to do about '
            f'it is a strategy decision. Return ColdStartVerdict(accounted_for=False) to '
            f'leave the decision with the framework. See '
            f'docs/architecture/live_execution_architecture.md.'
        )

    try:
        # The instance plus the situation — the two arguments the framework will pass.
        inspect.signature(decision_logic_class.on_cold_start).bind(object(), object())
    except TypeError as e:
        return (
            f"DecisionLogic '{decision_logic_class.__name__}' overrides on_cold_start() with "
            f'a signature the framework cannot call ({e}) — it is invoked as '
            f'on_cold_start(situation) and must accept exactly that.'
        )

    return None


def _overrides(decision_logic_class: Type[Any], hook_name: str) -> bool:
    """
    Whether a class replaced a base-class hook with its own.

    Identity comparison against the base: if the class's attribute IS the base's function,
    nothing was written. A class that does not have the attribute at all cannot have
    overridden it — and it is reachable, because this project resolves decision logics by
    STRING from configuration, so a class that never inherited from the base can arrive here
    and must produce the contract message rather than an AttributeError.

    Args:
        decision_logic_class: The class to inspect
        hook_name: The hook's attribute name

    Returns:
        True when the class (or one of its bases below AbstractDecisionLogic) defines it
    """
    own = getattr(decision_logic_class, hook_name, None)
    if own is None:
        return False

    return own is not getattr(AbstractDecisionLogic, hook_name)
