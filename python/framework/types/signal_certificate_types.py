"""
FiniexTestingIDE - Signal Feed Certificate Types
Typed structures behind the live signal feed release gate (#466).

Runtime domain types, hence dataclasses (§6). They separate three things the certificate
must not blur: what was ASSERTED (FeedCheck), what was merely OBSERVED and is recorded so
a later release can compare against it (ProducerIdentity, FeedObservation), and which
routes the run actually called (RouteCall) — the last of these is what makes "this run
spent nothing" a falsifiable property rather than a promise.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from python.framework.types.signal_data_types import (
    SignalSnapshot,
    SignalTransportKind,
)


@dataclass
class FeedCheck:
    """
    One contract assertion and its outcome.

    Args:
        name: Stable identifier of the assertion, usable to refer to a finding
        ok: Whether it held
        detail: What was measured — present on a pass too, because a certificate that
            only explains its failures cannot be compared against the next one
    """
    name: str
    ok: bool
    detail: str


@dataclass
class RouteCall:
    """
    One HTTP call the run made.

    Recorded per call rather than counted: the certifiable property is that the run touched
    only the producer's free routes, and a count cannot say which ones.

    Args:
        method: HTTP method
        path: Route path, without the host
    """
    method: str
    path: str

    def describe(self) -> str:
        """
        Operator-readable one-liner.

        Returns:
            Method and path
        """
        return f'{self.method} {self.path}'


@dataclass
class ProducerIdentity:
    """
    What /v1/health said about the engine and the store behind it.

    The id binds and the name does not — the id is a fingerprint of the producer's database
    cluster, fixed at its creation, while the name is looked up from a per-machine mapping
    on the producer side and may be renamed at any time. Certify against the id, read the
    name.

    Args:
        journal_id: Cluster fingerprint, None when the producer named no store
        environment: The producer's label for that journal
        engine_version: Producer version string
        pass_timeout_s: How long a producer pass may run
        cadence_seconds: How often the producer evaluates OUR source, as it reports it
        budget_suspended: The producer stopped evaluating because a spending limit was hit
    """
    journal_id: Optional[str] = None
    environment: str = ''
    engine_version: str = ''
    pass_timeout_s: Optional[float] = None
    cadence_seconds: Optional[float] = None
    budget_suspended: bool = False

    def is_identified(self) -> bool:
        """
        Whether the producer named a journal.

        Returns:
            True when an identity is known
        """
        return bool(self.journal_id)


@dataclass
class ProducerBuild:
    """
    What `/v1/build` said about the code behind the answering engine.

    Separate from ProducerIdentity because the two answer different questions: the identity
    says WHICH STORE the series lives in, this says WHICH CODE produced it. Measured
    2026-08-25: the producer deployed a new build while `version` stayed '0.3.3', so two
    certificates taken twenty minutes apart came from different code and the version string
    could not tell them apart. Only the commit binds — the same relationship the journal id
    has to the environment name.

    The route is public by the producer's default, like `/v1/health`, but behind a switch on
    their side: their repository is public today, so a commit hash discloses nothing that is
    not already on GitHub, and behind a private repository the same field would fingerprint
    known defects. `offered=False` is therefore a legitimate answer and not a contract
    violation.

    Args:
        offered: Whether the route answered at all
        version: Engine version string — coarse, and NOT a build identity
        commit: Short commit hash of the running build
        committed_at: When that commit was made
        dirty: Whether their tree carried uncommitted changes when built
        started_at: When the running process started — a change here between two
            certificates means the producer restarted between them, which is exactly when a
            sequence counter can be re-minted
        detail: Why nothing came back, when nothing did
    """
    offered: bool = False
    version: str = ''
    commit: str = ''
    committed_at: Optional[datetime] = None
    dirty: Optional[bool] = None
    started_at: Optional[datetime] = None
    detail: str = ''

    def describe(self) -> str:
        """
        Operator-readable one-liner.

        Returns:
            The build identity, or why it is unknown
        """
        if not self.offered:
            return f'not offered ({self.detail})' if self.detail else 'not offered'
        state = 'dirty' if self.dirty else 'clean'
        return f'{self.version} @ {self.commit} ({state})'


@dataclass
class FeedObservation:
    """
    One envelope as it was read, with the instant we read it.

    Keeps the raw mapping beside the parsed snapshot on purpose: half the contract is about
    what is on the WIRE (a field's location, a field's absence), and a parsed model can no
    longer answer that — `collected_msc` is absent on the wire and always present on the
    model, so only the raw mapping can prove it.

    Args:
        envelope: The raw decoded payload
        snapshot: The same envelope through the production reader
        fetched_at: When we read it. Wall clock, and legitimately so — this measures OUR
            observation (ts_init), never the event's own time (§9)
        frame_bytes: Encoded size of the payload
    """
    envelope: Dict[str, Any]
    snapshot: SignalSnapshot
    fetched_at: datetime
    frame_bytes: int

    def get_age_at_fetch_seconds(self) -> float:
        """
        How old the envelope already was when it reached us.

        Measured against the availability stamp, which is the instant it became fetchable
        at the producer — so this is the delay the interim pull transport costs, and the
        number the stream (#468) exists to remove.

        Returns:
            Age in seconds
        """
        return (self.fetched_at - self.snapshot.get_resolution_key()).total_seconds()


@dataclass
class FeedProbeResult:
    """
    Everything one certificate run read from the producer.

    Args:
        endpoint_name: Registered endpoint the run was AIMED at ('dev', 'production', …)
        base_url: Its address, as configured
        credential_source: File the token came from — never the token itself (§29)
        credential_configured: Whether a non-empty token was sent
        pipeline_id: Source that was read
        identity: What /v1/health reported, None when it did not answer
        build: What /v1/build reported about the running code
        observations: The envelopes read, in read order
        routes_used: Every route the run called
        transport: The transport that performed these reads — recorded, never declared
        transport_failures: Reads that did not come back, as failed checks
        unparsed_envelopes: Payloads our reader REFUSED, kept raw. The shape checks still
            run over these, because 'the reader refused it' without naming the field that
            disagreed is how the last such rejection got filed as the producer's outage
    """
    endpoint_name: str
    base_url: str
    credential_source: str
    credential_configured: bool
    pipeline_id: str
    # Which transport actually did the reading. Required and without a default on purpose:
    # this used to be a module CONSTANT written straight into the artifact, so a certificate
    # taken over the stream would have claimed 'poll' — the same defect as an adapter
    # certificate that re-read a config file instead of recording what its run did. A
    # default here would be the same mistake wearing a type annotation, so the observer that
    # performed the reads has to say what it is.
    transport: SignalTransportKind
    identity: Optional[ProducerIdentity] = None
    build: ProducerBuild = field(default_factory=ProducerBuild)
    observations: List[FeedObservation] = field(default_factory=list)
    routes_used: List[RouteCall] = field(default_factory=list)
    transport_failures: List[FeedCheck] = field(default_factory=list)
    unparsed_envelopes: List[Dict[str, Any]] = field(default_factory=list)

    def is_readable(self) -> bool:
        """
        Whether the run got what the contract checks need.

        Returns:
            True when the producer identified itself and at least one envelope was read
        """
        return (self.identity is not None
                and self.identity.is_identified()
                and bool(self.observations))


@dataclass
class SignalFeedAssessment:
    """
    Every verdict and every recorded value one certificate run produced.

    The split between the two is deliberate and load-bearing. `checks` decide PASS/FAIL;
    the recorded fields decide nothing and exist so a LATER release can compare against
    this one — a changed config fingerprint or prompt version is a comparability break the
    operator should see, not a failure that turns a release red.

    Args:
        probe: What the run read from the producer
        checks: Every assertion, in evaluation order
        unread_fields: Wire fields our reader does not consume
        unknown_vocabulary: Closed-vocabulary values we do not know yet
        rows_without_evidence: Rows resting on no evidence at all
        cadence_minutes_configured: What we have registered for this source
        stream_seconds_held: How long the run held the stream open
        previous_certificate: Artifact this run was compared against, None for the first
    """
    probe: FeedProbeResult
    checks: List[FeedCheck] = field(default_factory=list)
    unread_fields: List[str] = field(default_factory=list)
    unknown_vocabulary: List[str] = field(default_factory=list)
    rows_without_evidence: int = 0
    cadence_minutes_configured: Optional[float] = None
    stream_seconds_held: float = 0.0
    previous_certificate: Optional[str] = None

    def is_passed(self) -> bool:
        """
        Whether every assertion held.

        Returns:
            True when no check failed
        """
        return all(check.ok for check in self.checks)

    def get_failed(self) -> List[FeedCheck]:
        """
        The assertions that did not hold.

        Returns:
            Failed checks in evaluation order
        """
        return [check for check in self.checks if not check.ok]

    def get_seq_span(self) -> Tuple[Optional[int], Optional[int]]:
        """
        Lowest and highest seq observed.

        Returns:
            (first, last), both None when no envelope carried a stream position
        """
        sequences = [o.snapshot.seq for o in self.probe.observations
                     if o.snapshot.seq is not None]
        if not sequences:
            return (None, None)
        return (min(sequences), max(sequences))
