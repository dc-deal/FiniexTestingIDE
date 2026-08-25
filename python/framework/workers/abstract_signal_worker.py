"""
FiniexTestingIDE - Abstract Signal Worker
Base class for SIGNAL workers — pre-collected external data lookup (#141)
"""

from abc import abstractmethod
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from python.framework.exceptions.signal_data_errors import SignalProviderNotInjectedError
from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.market_types.market_types import TradingContext
from python.framework.types.parameter_types import InputParamDef
from python.framework.types.signal_data_types import (
    ResolvedSignal,
    SignalEdge,
    SignalEpisodeEdge,
    SignalResolution,
)
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class AbstractSignalWorker(AbstractWorker):
    """
    Base class for SIGNAL workers — values looked up from pre-collected external
    data by timestamp, not computed from bars.

    Lookup-centric: a SIGNAL worker holds an injected SignalDataProvider and, on
    each tick, resolves the most recent snapshot with collected_msc <= tick. It
    refreshes only when the tick crosses into a new snapshot window
    (should_refresh) — the analogue of an INDICATOR's bar-update recompute. No
    warmup, no timeframes, no compute basis. The live API/EVENT refresh path
    rides #375; should_refresh is the forward-compatible seam.
    """

    # The signal PAYLOAD KIND this worker consumes (e.g. 'llm_sentiment') — the slot the
    # data-preparation layer hands the matching series to. Deliberately NOT called a
    # "source": the source is the archive identity (pipeline_id = data_sentiment_type), and
    # one kind is read from many sources. Concrete workers set it.
    CONSUMED_SIGNAL_KIND: str = ''

    def __init__(
        self,
        name: str,
        logger,
        parameters=None,
        trading_context: TradingContext = None
    ):
        """
        Initialize signal worker.

        Args:
            name: Worker name/identifier
            logger: ScenarioLogger instance (REQUIRED)
            parameters: ValidatedParameters or dict (auto-wrapped)
            trading_context: TradingContext (provides the scenario symbol)
        """
        super().__init__(
            name=name, logger=logger,
            parameters=parameters, trading_context=trading_context
        )
        # The scenario symbol whose per-symbol result this instance reads.
        self._symbol: Optional[str] = (
            trading_context.symbol if trading_context else None
        )
        # Injected by the framework at construction (sim subprocess / live boot).
        self._signal_provider: Optional[SignalDataProvider] = None
        # collected_msc of the last served snapshot (refresh-window tracking).
        self._last_snapshot_msc = None
        # Staleness of the last served result (staleness-flip refresh tracking, #434).
        self._last_served_stale: Optional[bool] = None
        # Resolution class of the last evaluated result (#433 Part C counters).
        self._last_resolution: SignalResolution = SignalResolution.BLIND
        # RC-4 (#141 Part 2a): the producer runs passes concurrently, so a pass that runs
        # long commits AFTER a later one and carries the higher seq while resting on older
        # evidence. Tracking the evidence of the previously served envelope is what lets a
        # decision tell a genuine change from an overtaking pass. Envelope-level: a row's
        # stamp may fall legitimately between passes, an envelope's may not.
        self._last_served_evidence: Optional[datetime] = None
        self._evidence_regressed: bool = False
        # Last OBSERVED value of the property a concrete worker derives an edge from.
        # None until the first observation, and left untouched by a gap or an overtaking
        # pass — see _derive_edge.
        self._last_edge_value: Optional[bool] = None
        self._last_episode_id: Optional[str] = None

    def set_signal_provider(self, provider: SignalDataProvider) -> None:
        """
        Inject the signal data provider (framework collaborator).

        Args:
            provider: SignalDataProvider built from the prepared signal series
        """
        self._signal_provider = provider

    def get_signal_provider(self) -> Optional[SignalDataProvider]:
        """
        The injected provider, or None before injection.

        Returns:
            The SignalDataProvider this worker resolves against
        """
        return self._signal_provider

    def _require_provider(self) -> SignalDataProvider:
        """
        Return the injected provider or fail loudly (no silent fallback).

        Returns:
            The injected SignalDataProvider

        Raises:
            SignalProviderNotInjectedError: If no provider was injected
        """
        if self._signal_provider is None:
            raise SignalProviderNotInjectedError(
                f"SIGNAL worker '{self.name}' has no injected SignalDataProvider. "
                f"It must be built from the prepared signal series and injected at "
                f"construction (sim subprocess / live boot)."
            )
        return self._signal_provider

    @classmethod
    def get_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """
        SIGNAL contract params merged over the worker's domain params.

        max_staleness_minutes and data_path are TYPE-level (every SIGNAL feed
        ages; every archive can be dev-overridden) — declared ONCE here so no
        concrete worker can forget them. Concrete workers declare their own
        params via _get_domain_parameter_schema(); all consumers (factory
        validation, defaults, tooling) keep reading THIS method — the config
        JSON surface stays fully visible.

        Returns:
            Dict[param_name, InputParamDef]
        """
        return {
            **cls._get_domain_parameter_schema(),
            'max_staleness_minutes': InputParamDef(
                param_type=int,
                default=30,
                min_val=1,
                description='Snapshot age (tick − collected_msc) above which the '
                            'result envelope is flagged is_stale',
            ),
            'signal_delay_minutes': InputParamDef(
                param_type=int,
                default=0,
                min_val=0,
                description='Artificial resolution delay: resolve as-of (now − delay). '
                            'A robustness lever for sweeps, NOT a model of the archive — '
                            'the archive carries no unrecorded delay',
            ),
            'data_path': InputParamDef(
                param_type=str,
                default='',
                description='Optional explicit signal archive path '
                            '(dev override; empty = resolved via the data source)',
            ),
        }

    @classmethod
    def _get_domain_parameter_schema(cls) -> Dict[str, InputParamDef]:
        """
        Domain-specific parameters of the concrete SIGNAL worker.

        Returns:
            Dict[param_name, InputParamDef] (empty when the contract params suffice)
        """
        return {}

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        """SIGNAL — pre-collected external data lookup."""
        return WorkerType.SIGNAL

    @classmethod
    def get_consumed_signal_kind(cls) -> str:
        """The signal payload kind this worker consumes (e.g. 'llm_sentiment')."""
        return cls.CONSUMED_SIGNAL_KIND

    @classmethod
    def get_required_activity_metric(cls) -> Optional[str]:
        """SIGNAL workers read external data — no market-activity dependency."""
        return None

    def get_warmup_requirements(self) -> Dict[str, int]:
        """SIGNAL workers have no warmup — data is looked up by timestamp."""
        return {}

    def get_required_timeframes(self) -> List[str]:
        """SIGNAL workers consume no bar timeframes."""
        return []

    def _resolve_at(self, now: datetime) -> datetime:
        """
        The moment the provider is queried at, which is not always the current moment.

        signal_delay_minutes shifts it backwards so a run can be swept against a slower
        feed than the archive actually delivered. Staleness stays measured against the
        REAL moment: a delayed resolution genuinely serves an older snapshot, and saying
        otherwise would hide the very cost the sweep exists to measure.

        Args:
            now: Current moment (canonical clock, UTC)

        Returns:
            The as-of moment to resolve at — `now` itself when no delay is configured
        """
        delay_minutes = self.params.get('signal_delay_minutes')
        if not delay_minutes:
            return now
        return now - timedelta(minutes=delay_minutes)

    def _derive_edge(self, observed: Optional[bool]) -> SignalEdge:
        """
        Transition of a boolean property against the previously observed envelope.

        Three cases yield NONE and leave the remembered state untouched, each for its
        own reason:

        - **No previous observation.** A session that boots into an active state has not
          witnessed an entry; reporting one would make a boot look like an event.
        - **A gap.** Nothing resolvable means the state is UNKNOWN, not False. Reading a
          gap as False would emit an exit on the way in and an entry on the way out —
          two transitions that never happened.
        - **An overtaking pass (RC-4).** An envelope resting on older evidence did not
          witness what came after it. Letting it flip the edge would turn the producer's
          commit order into a phantom transition, which is exactly what the
          evidence-regression flag exists to prevent one level up.

        Args:
            observed: The property's value in this envelope, None on a gap

        Returns:
            The transition, or NONE when there is none to report
        """
        if observed is None or self._evidence_regressed:
            return SignalEdge.NONE
        previous = self._last_edge_value
        self._last_edge_value = observed
        if previous is None or previous == observed:
            return SignalEdge.NONE
        return SignalEdge.ENTERED if observed else SignalEdge.EXITED

    def _derive_episode_edge(self, observed: Optional[str]) -> SignalEpisodeEdge:
        """
        Transition of the breaking-episode identity against the previous envelope.

        Mirrors `_derive_edge`'s restraint exactly — no previous observation, a gap and an
        overtaking pass all report NONE, for the same three reasons — and adds the case a
        boolean cannot carry: one episode replaced by another with no quiet pass between,
        which `is_breaking` reports as no change at all.

        The identity itself is the producer's, and it is treated as OPAQUE: compared for
        equality, never parsed. Its empty value means "no episode", which is why an empty id
        read against a remembered one is a close rather than a change.

        Args:
            observed: The episode id in this envelope (empty outside an episode), None on a gap

        Returns:
            The transition, or NONE when there is none to report
        """
        if observed is None or self._evidence_regressed:
            return SignalEpisodeEdge.NONE
        previous = self._last_episode_id
        self._last_episode_id = observed
        if previous is None or previous == observed:
            return SignalEpisodeEdge.NONE
        if not previous:
            return SignalEpisodeEdge.OPENED
        return SignalEpisodeEdge.CLOSED if not observed else SignalEpisodeEdge.CHANGED

    def should_refresh(self, tick: TickData) -> bool:
        """
        Whether the worker should recompute its result this tick.

        Args:
            tick: Current tick

        Returns:
            True if the worker should recompute its result this tick
        """
        return self.should_refresh_at(tick.timestamp)

    def should_refresh_at(self, now: datetime) -> bool:
        """
        Whether the worker should recompute its result at a moment.

        Two triggers (#434): the moment crossed into a NEW snapshot window (cold
        start included), OR the staleness of the served result FLIPPED (the feed
        died mid-session — the snapshot stops changing, but its age crosses the
        staleness boundary; without this trigger the cached result would stay
        fresh-flagged forever).

        Time-based rather than tick-based so an off-tick arrival can drive a refresh
        on the heartbeat without a synthetic tick being fabricated (#141 Part 2a).

        Args:
            now: Moment to evaluate at (canonical clock, UTC)

        Returns:
            True if the worker should recompute its result
        """
        resolved = self._require_provider().nearest(self._resolve_at(now), self._symbol)
        current_msc = resolved.collected_msc if resolved else None
        if current_msc != self._last_snapshot_msc:
            return True
        stale = self._evaluate_stale(resolved, now)
        # No refresh ahead → the cached result is what this pass serves, so its
        # resolution class is recorded here for the per-tick counter (#433 Part C).
        self._last_resolution = self._classify(resolved, stale)
        return stale != self._last_served_stale

    def compute_signal(self, tick: TickData) -> WorkerResult:
        """
        Resolve the point-in-time signal for this tick and map it to a WorkerResult.

        Args:
            tick: Current tick

        Returns:
            WorkerResult with outputs matching get_output_schema()
        """
        return self.compute_signal_at(tick.timestamp)

    def compute_signal_at(self, now: datetime) -> WorkerResult:
        """
        Resolve the point-in-time signal at a moment and map it to a WorkerResult.

        Looks up the most recent snapshot available at or before the moment, records
        its stamp + staleness for refresh tracking, and delegates field mapping to
        the concrete worker (_build_result). A gap (nothing resolvable) yields an
        empty result via _build_result(None, now).

        Args:
            now: Moment to resolve at (canonical clock, UTC)

        Returns:
            WorkerResult with outputs matching get_output_schema()
        """
        resolved = self._require_provider().nearest(self._resolve_at(now), self._symbol)
        stale = self._evaluate_stale(resolved, now)
        self._evidence_regressed = self._evaluate_evidence_regression(resolved)
        self._last_snapshot_msc = resolved.collected_msc if resolved else None
        self._last_served_stale = stale
        self._last_resolution = self._classify(resolved, stale)
        # Envelope stamp (#434): the framework owns the feed-status channel —
        # the payload mapping (_build_result) never sets it.
        result = self._build_result(resolved, now)
        result.is_stale = stale
        return result

    def _evaluate_evidence_regression(
        self, resolved: Optional[ResolvedSignal]
    ) -> bool:
        """
        Whether this envelope rests on OLDER evidence than the one served before it (RC-4).

        True means the producer's passes overtook each other: a longer-running pass
        committed after a later one, so it carries the newer position in the series and the
        older view of the world. The information is still valid — it is not discarded — but
        a decision must not read it as a CHANGE, or it reacts to a reversal that only ever
        happened in the ordering.

        Deliberately not a key: resolution stays anchored to when a snapshot became
        available. Resolving by evidence time would be look-ahead — evidence gathered at
        10:09 that only became available at 10:12 was not ours to use at 10:09.

        Args:
            resolved: The point-in-time signal, or None on a gap

        Returns:
            True when the envelope's evidence predates the previously served envelope's
        """
        if resolved is None or resolved.evidence_as_of is None:
            return False
        previous = self._last_served_evidence
        self._last_served_evidence = resolved.evidence_as_of
        if previous is None:
            return False
        return resolved.evidence_as_of < previous

    def get_evidence_regressed(self) -> bool:
        """
        Whether the last served envelope rested on older evidence than its predecessor.

        Returns:
            True on an overtaking producer pass (RC-4)
        """
        return self._evidence_regressed

    def _evaluate_stale(self, resolved: Optional[ResolvedSignal], now: datetime) -> bool:
        """
        Whether the resolved signal counts as stale at this moment (#434).

        The ONE staleness definition per worker — should_refresh_at (flip trigger)
        and the result envelope both read it. Default: a gap, or a snapshot
        older than max_staleness_minutes (the type-level contract param) —
        every SIGNAL worker gets age-based staleness out of the box. Override
        for source-specific semantics (e.g. event expiry instead of age).

        Args:
            resolved: The point-in-time signal, or None on a gap
            now: Moment to evaluate at (age reference, canonical clock)

        Returns:
            True if the signal is stale at this moment
        """
        if resolved is None:
            return True
        age_minutes = (now - resolved.collected_msc).total_seconds() / 60.0
        return age_minutes > self.params.get('max_staleness_minutes')

    def _classify(
        self,
        resolved: Optional[ResolvedSignal],
        stale: bool
    ) -> SignalResolution:
        """
        Classify a resolved signal for the per-tick resolution counters (#433).

        Splits the two cases _evaluate_stale collapses into one True: a real
        snapshot that aged out (STALE) versus nothing resolvable at all (BLIND).

        Args:
            resolved: The point-in-time signal, or None when nothing resolved
            stale: The staleness verdict for that signal

        Returns:
            The resolution class of this result
        """
        if resolved is None:
            return SignalResolution.BLIND
        return SignalResolution.STALE if stale else SignalResolution.FRESH

    def get_last_resolution(self) -> SignalResolution:
        """
        Resolution class of the result this worker currently serves (#433).

        Returns:
            The last evaluated SignalResolution
        """
        return self._last_resolution

    def get_symbol(self) -> Optional[str]:
        """
        The scenario symbol this instance reads its per-symbol result for.

        Returns:
            The symbol, or None when no trading context was injected
        """
        return self._symbol

    @abstractmethod
    def _build_result(
        self,
        resolved: Optional[ResolvedSignal],
        now: datetime
    ) -> WorkerResult:
        """
        Map a resolved signal (or a gap) to this worker's WorkerResult.

        Args:
            resolved: The point-in-time signal, or None on a gap (nothing resolvable)
            now: Moment being resolved at (canonical clock)

        Returns:
            WorkerResult with outputs matching get_output_schema()
        """
        pass
