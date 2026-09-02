"""
FiniexTestingIDE - Cold-Start Setup (#355 Phase 2)

Builds the cold-start subsystem and runs the boot step, so `AutotraderMain.run()` keeps a call
instead of a block. The same shape the tick source and the signal transport already use
(`tick_source_setup`, `signal_transport_setup`): one function that constructs, wires, decides,
and hands back what the session has to hold on to.

What lives here rather than in main: which sessions are eligible at all, the Field Study
exclusion, the dry-run resolution, and the boot step itself. What stays in main: the call, and
what to do when the answer is "do not start".
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Set

from python.framework.autotrader.cold_start_adopter import ColdStartAdopter
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.persistence.cold_start_state_store import ColdStartStateStore
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig


@dataclass
class ColdStartSetup:
    """
    What a session keeps after the cold-start boot step.

    Lives beside its builder rather than in `framework/types/` (§6): `store` is a live
    collaborator, and the type module could not import it without a cycle.

    Args:
        proceed: False when the session must not start — the reason is already in the
            session log, and therefore in the error pot (§35)
        store: The carry-over, or None when this session has none (Field Study, disabled,
            or a simulation executor)
        persist: Whether this session may WRITE the carry-over. False for a dry run, which
            sent no order to any venue, and False for a boot that never got through
        keys_in_use: Session discriminators the venue currently shows on orders of our shape.
            Protected from eviction, so the key that owns a resting order cannot age out
        adopted_count: How many resting orders were rebuilt
    """
    proceed: bool = True
    store: Optional[ColdStartStateStore] = None
    persist: bool = False
    keys_in_use: Set[str] = field(default_factory=set)
    adopted_count: int = 0


def setup_cold_start(
    config: AutoTraderConfig,
    executor: AbstractTradeExecutor,
    decision_logic: AbstractDecisionLogic,
    logger: AbstractLogger,
    run_id: str,
    attended: bool,
    field_study_active: bool,
    dry_run: bool,
) -> ColdStartSetup:
    """
    Run the cold-start boot step and hand back what the session keeps.

    The venue still holds what an earlier session left resting, and a bot that starts anyway
    trades beside its own open orders without seeing them. This rebuilds what is provably OURS
    — the client order id says so — and refuses to start when it cannot answer for what it
    found. Balances are never touched here: a coin carries no owner tag, so what a bot may use
    is declared capital, a different mechanism entirely.

    Args:
        config: The resolved profile — `cold_start` block, adapter type, dry-run flag
        executor: The session's executor; only a live one has broker truth to pull
        decision_logic: Used only to recognise the Field Study exclusion
        logger: Session logger — the channel that reaches the run outcome (§35)
        run_id: Recorded as PROVENANCE in the carry-over, never as its key
        attended: A human DECLARED they are watching this start (`--attended`)
        field_study_active: True when the Field Study owns this session
        dry_run: The session's RESOLVED dry-run state, passed in rather than re-derived.
            Deriving it from `config.dry_run` here would read the profile field alone and
            miss the broker's standing posture — which is precisely the near miss #304 exists
            for: a profile said `dry_run: true`, the broker override said false, and the
            profile field was read by nothing

    Returns:
        A ColdStartSetup; `proceed=False` means the session must not start
    """
    if not config.cold_start.enabled or not isinstance(executor, LiveTradeExecutor):
        return ColdStartSetup()

    if field_study_active:
        # It funds both sides on purpose and asserts its own flat order book (#332), so
        # adoption would claim its funding. Said out loud rather than done silently.
        logger.info(
            '🧬 Cold start skipped — the Field Study funds both sides on purpose and asserts '
            'a flat order book itself (#332).'
        )
        return ColdStartSetup()

    store = ColdStartStateStore(
        root=Path(config.cold_start.path),
        profile=config.name or config.symbol,
        symbol=config.symbol,
        logger=logger,
        run_id=run_id,
    )
    adopter = ColdStartAdopter(
        executor=executor,
        store=store,
        config=config.cold_start,
        symbol=config.symbol,
        logger=logger,
        dry_run=dry_run,
        interactive=attended,
    )

    if not adopter.run():
        # The loud banner is already in the session log. `persist` stays False, and that is
        # the point: a refused boot must NOT append its key. Otherwise a restart loop — the
        # refusal grades the run non-zero, a supervisor relaunches — consumes the key window
        # with its own aborts and evicts the key that owns the very order it kept refusing
        # over, after which the bot stops refusing and trades beside it.
        return ColdStartSetup(proceed=False, store=store)

    return ColdStartSetup(
        proceed=True,
        store=store,
        persist=not dry_run,
        keys_in_use=adopter.get_venue_session_keys(),
        adopted_count=adopter.get_adopted_count(),
    )
