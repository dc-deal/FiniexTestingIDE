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
from functools import partial
from pathlib import Path
from typing import Optional, Set

from python.configuration.data_home import resolve_in_data_home
from python.framework.autotrader.cold_start.cold_start_adopter import ColdStartAdopter
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.persistence.cold_start_state_store import ColdStartStateStore
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
    ConfigurationComparison,
    CurrentConfiguration,
)
from python.framework.types.persistence_types import (
    CarryOverLoad,
    CarryOverLoadOutcome,
    CarryOverRead,
    ColdStartPayload,
)
from python.framework.types.validation_types import ValidationFinding
from python.framework.validators.carry_over_configuration_validator import (
    compare_carried_configuration,
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
        persist: Whether this session may WRITE the carry-over at all. False only for a boot
            that never got through — a refused boot must not append its key, or a restart
            loop consumes its own key window
        persist_venue_claims: Whether it may write the half of the payload that makes a CLAIM
            ABOUT THE VENUE — the session key and the open position book. False for a dry run,
            which sent no order anywhere, so the venue holds nothing those fields describe.
            The other half — the risk baseline, the reported drawdown curve, the deployment
            identity — is OUR OWN record: numbers we computed, true whether or not the venue
            was real, and written either way
        keys_in_use: Session discriminators the venue currently shows on orders of our shape.
            Protected from eviction, so the key that owns a resting order cannot age out
        situation: What the boot found, as the decision logic saw it — None for a dry run,
            an unreachable venue, or a session with no cold start at all
        verdict: What the decision logic answered, when it was asked
        refusal: Why the boot was refused, as the session log says it — '' when it was not
        configuration_comparison: How the session's configuration compared with the one the
            claims it takes over were made under (#626) — None when no comparison was made
        configuration_finding: What that comparison leaves on the run's record, when anything
        acceptance_notice: The notice of a change `--accept-composite-change` let through, for
            the console — '' when none was
    """
    proceed: bool = True
    store: Optional[ColdStartStateStore] = None
    persist: bool = False
    persist_venue_claims: bool = False
    keys_in_use: Set[str] = field(default_factory=set)
    situation: Optional[ColdStartSituation] = None
    verdict: Optional[ColdStartVerdict] = None
    refusal: str = ''
    configuration_comparison: Optional[ConfigurationComparison] = None
    configuration_finding: Optional[ValidationFinding] = None
    acceptance_notice: str = ''


def setup_cold_start(
    config: AutoTraderConfig,
    executor: AbstractTradeExecutor,
    decision_logic: AbstractDecisionLogic,
    logger: AbstractLogger,
    run_id: str,
    attended: bool,
    adoption_skipped_by: Optional[str],
    dry_run: bool,
    session_end_orders: str = 'cancel',
    current_configuration: Optional[CurrentConfiguration] = None,
    accept_composite_change: Optional[str] = None,
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
        adoption_skipped_by: The acceptance study that asserts its own flat book and so takes no
            adoption, by its name ('Field Study'); None when adoption runs
        dry_run: The session's RESOLVED dry-run state, passed in rather than re-derived.
            Deriving it from `config.dry_run` here would read the profile field alone and
            miss the broker's standing posture — which is precisely the near miss #304 exists
            for: a profile said `dry_run: true`, the broker override said false, and the
            profile field was read by nothing
        session_end_orders: The resolved session-end policy for the ORDERS axis (#492). The
            adoption prompt states what will happen to the very orders it asks about, so it
            is told rather than left asserting the unconditional cleanup that used to exist
        current_configuration: What the session runs with, held against the configuration
            the claims it takes over were made under (#626); None compares nothing
        accept_composite_change: The composite this one start accepts
            (`--accept-composite-change <key>`); None when the flag was not given

    Returns:
        A ColdStartSetup; `proceed=False` means the session must not start
    """
    if not config.cold_start.enabled or not isinstance(executor, LiveTradeExecutor):
        return ColdStartSetup()

    if adoption_skipped_by is not None:
        # It funds both sides on purpose and asserts its own flat order book (#332), so
        # adoption would claim its funding. Said out loud rather than done silently.
        logger.info(
            f'🧬 Cold start skipped — the {adoption_skipped_by} funds both sides on purpose and '
            f'asserts a flat order book itself (#332).'
        )
        return ColdStartSetup()

    store = _carry_over_store(config, logger, run_id)
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
        exclusive_account=config.capital.exclusive_account,
        compare_configuration=(
            partial(compare_carried_configuration, current=current_configuration)
            if current_configuration is not None else None),
        accept_composite_change=accept_composite_change,
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
            refusal=adopter.get_refusal(),
            configuration_comparison=adopter.get_configuration_comparison(),
        )

    return ColdStartSetup(
        proceed=True,
        store=store,
        # A session treated as dry writes too, but only what it can honestly claim. Splitting
        # the two halves is what makes the mechanism rehearsable at all: a mock session is
        # always treated as dry (`_is_dry_run` answers on the adapter type), so before this the
        # deployment identity and the drawdown curve could not be exercised without a real
        # venue — while the reason for the old blanket refusal only ever applied to the session
        # KEY and the position BOOK, which describe orders the venue does not hold.
        persist=True,
        persist_venue_claims=not dry_run,
        keys_in_use=adopter.get_venue_session_keys(),
        situation=adopter.get_situation(),
        verdict=adopter.get_verdict(),
        configuration_comparison=adopter.get_configuration_comparison(),
        configuration_finding=adopter.get_configuration_finding(),
        acceptance_notice=adopter.get_acceptance_notice(),
    )


def describe_carry_over_read(load: Optional[CarryOverLoad], first_start: bool) -> str:
    """
    One line naming the carry-over a session continued, or why it continued none (#598).

    Args:
        load: What the boot's read found; None when the cold start is switched off
        first_start: Whether the start was declared the bot's first

    Returns:
        The line, for the session channel before the first order
    """
    if load is None:
        return 'Carry-over: not read — the cold start is switched off'
    if load.outcome is CarryOverLoadOutcome.ACCEPTED:
        writer = f'run {load.writer_run_id}' if load.writer_run_id else 'a run that recorded no id'
        return (f'Carry-over: continued from {writer} of {load.writer_profile}, written '
                f'{load.saved_at_utc} — {load.path}')
    declared = ' — the start is declared the bot\'s first (--first-start)' if first_start else ''
    if load.outcome is CarryOverLoadOutcome.ABSENT:
        return f'Carry-over: none at {load.path}{declared}'
    return (f'Carry-over: found but not continued ({load.outcome.value}) at '
            f'{load.path}{declared}')


def read_carry_over(config: AutoTraderConfig) -> CarryOverRead:
    """
    Open this bot's carry-over document before anything else needs it.

    Early because the run HEADER carries the deployment identity and is written at the
    run's start with no update path. Constructing the store does no IO — it assembles a
    path. The deployment, the risk baseline, the drawdown curve and the period floor read
    this payload; the cold-start adopter reads the document again for its own step, and only
    that read's warnings reach the session log — this one comes before the session logger
    exists. What the read FOUND travels with it: the boot's continuity checks decide on it
    before anything is written (#358).

    Args:
        config: The resolved profile — its `cold_start` block and the bot's identity

    Returns:
        The stored payload, or an empty one when there is nothing to read, with what the read
        found — None when cold start is disabled and nothing was read
    """
    if not config.cold_start.enabled:
        return CarryOverRead(ColdStartPayload())
    store = _carry_over_store(config, get_global_logger(), run_id=None)
    payload = store.load()
    return CarryOverRead(payload, store.get_last_load())


def _carry_over_store(
    config: AutoTraderConfig,
    logger: AbstractLogger,
    run_id: Optional[str],
) -> ColdStartStateStore:
    """
    This bot's carry-over document — one recipe for the boot's read and the session's write,
    so the document read at boot and the one the session writes are addressed the same way.

    Args:
        config: The resolved profile — its `cold_start` block and the bot's identity
        logger: Where the store reports its own warnings
        run_id: Recorded as PROVENANCE in what the store writes; None for the boot's read

    Returns:
        The store; constructing it does no IO
    """
    return ColdStartStateStore(
        root=resolve_in_data_home(Path(config.cold_start.path)),
        profile=config.get_unit_name(),
        symbol=config.symbol,
        logger=logger,
        run_id=run_id,
        bot_id=config.bot_id,
        # In this one recipe, so the boot's early read, the adopter's read and every write
        # agree on whose documents are this bot's (#358) — a continuation honoured by only one
        # of them would adopt the book and miss the baseline, or the other way round.
        carry_over_from=config.carry_over_from,
    )
