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
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
)


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
        situation: What the boot found, as the decision logic saw it — None for a dry run,
            an unreachable venue, or a session with no cold start at all
        verdict: What the decision logic answered, when it was asked
    """
    proceed: bool = True
    store: Optional[ColdStartStateStore] = None
    persist: bool = False
    keys_in_use: Set[str] = field(default_factory=set)
    situation: Optional[ColdStartSituation] = None
    verdict: Optional[ColdStartVerdict] = None


def setup_cold_start(
    config: AutoTraderConfig,
    executor: AbstractTradeExecutor,
    decision_logic: AbstractDecisionLogic,
    logger: AbstractLogger,
    run_id: str,
    attended: bool,
    field_study_active: bool,
    dry_run: bool,
    session_end_orders: str = 'cancel',
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
        decision_logic: Recognises the Field Study exclusion, and answers for the boot
            situation itself (#493)
        logger: Session logger — the channel that reaches the run outcome (§35)
        run_id: Recorded as PROVENANCE in the carry-over, never as its key
        attended: A human DECLARED they are watching this start (`--attended`)
        field_study_active: True when the Field Study owns this session
        dry_run: The session's RESOLVED dry-run state, passed in rather than re-derived.
            Deriving it from `config.dry_run` here would read the profile field alone and
            miss the broker's standing posture — which is precisely the near miss #304 exists
            for: a profile said `dry_run: true`, the broker override said false, and the
            profile field was read by nothing
        session_end_orders: The resolved session-end policy for the ORDERS axis (#492). The
            adoption prompt states what will happen to the very orders it asks about, so it
            is told rather than left asserting the unconditional cleanup that used to exist

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
        decision_logic=decision_logic,
        session_end_orders=session_end_orders,
    )

    if not adopter.run():
        # The loud banner is already in the session log. `persist` stays False, and that is
        # the point: a refused boot must NOT append its key. Otherwise a restart loop — the
        # refusal grades the run non-zero, a supervisor relaunches — consumes the key window
        # with its own aborts and evicts the key that owns the very order it kept refusing
        # over, after which the bot stops refusing and trades beside it.
        # The situation and the verdict travel even here — ESPECIALLY here. A refused boot is
        # the outcome that matters most to whoever reads the run afterwards, and dropping it
        # would leave the run record silent about the one session that declined to trade.
        return ColdStartSetup(
            proceed=False,
            store=store,
            situation=adopter.get_situation(),
            verdict=adopter.get_verdict(),
        )

    return ColdStartSetup(
        proceed=True,
        store=store,
        persist=not dry_run,
        keys_in_use=adopter.get_venue_session_keys(),
        situation=adopter.get_situation(),
        verdict=adopter.get_verdict(),
    )
