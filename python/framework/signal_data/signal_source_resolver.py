"""
FiniexTestingIDE - Signal Source Resolver
Decides ONCE what feeds a session's SIGNAL workers (#141).

The question has three answers and they must be asked in one order:

    Does this setup have a SIGNAL worker?
      no  -> NONE      the signal settings do not apply to this profile at all
      yes -> Is a prepared series mounted (mock / simulation)?
               yes -> MOUNTED   the archive is the source; no connection is opened
               no  -> LIVE      a transport must fill the workers

Every site that used to ask this for itself now reads the resolved answer. That is the
whole point of the unit: the previous arrangement had three separate derivations, and two
of them were wrong — one aborted a session that needed no source, the other opened a live
connection into a session that already carried a mounted one.
"""

from typing import List, Optional

from python.framework.exceptions.signal_data_errors import SignalSourceUnresolvedError
from python.framework.types.config_types.sentiment_config_types import SentimentConfig
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.types.signal_data_types import (
    SignalSourceMode,
    SignalSourceResolution,
    SignalTransportKind,
)
from python.framework.workers.abstract_signal_worker import AbstractSignalWorker


class SignalSourceResolver:
    """
    Resolves the signal source mode for one session.

    Stateless by design: it reads the session's inputs and returns a verdict. Nothing is
    started, injected or connected here — the callers do that, each according to the mode.
    """

    @staticmethod
    def resolve(
        workers: List,
        package: Optional[ProcessDataPackage],
        sentiment_config: SentimentConfig,
    ) -> SignalSourceResolution:
        """
        Answer which source feeds this session's SIGNAL workers.

        Args:
            workers: The session's worker instances (SIGNAL workers are picked out here)
            package: Prepared scenario data package — mock/simulation mounts its series
                from this; None for a live session
            sentiment_config: The installation's signal transport settings

        Returns:
            The resolved mode, with the live signal kind and transport when mode is LIVE
        """
        signal_workers = [w for w in workers if isinstance(w, AbstractSignalWorker)]

        # First question. An installation-wide transport setting says nothing about a
        # profile that reads no signals, so it must not reach this profile's startup.
        if not signal_workers:
            return SignalSourceResolution(
                mode=SignalSourceMode.NONE,
                worker_count=0,
                reason='No SIGNAL worker in this profile — no signal source needed')

        # Second question. The PRESENCE of a package decides, never its contents: a mock
        # replay and a simulation are reproducible precisely because nothing arrives from
        # outside, so a package whose series is empty must fail as a wiring problem rather
        # than quietly reach for a live transport. Injection keeps its own early return, so
        # an empty series still surfaces where it did before.
        if package is not None:
            return SignalSourceResolution(
                mode=SignalSourceMode.MOUNTED,
                worker_count=len(signal_workers),
                reason=(f'{len(signal_workers)} SIGNAL worker(s) read the mounted '
                        f'scenario series — no live transport'))

        # Third question: live. The workers must agree on one kind, because one transport
        # carries one source (#258 is the multi-source binding).
        signal_kinds = {worker.get_consumed_signal_kind() for worker in signal_workers}
        if len(signal_kinds) > 1:
            raise SignalSourceUnresolvedError(
                f'A live signal transport serves one source, but the workers consume '
                f'{sorted(signal_kinds)}. Multi-source binding is #258.')

        transport = SignalSourceResolver._resolve_transport(sentiment_config)
        if transport is None:
            names = ', '.join(f"'{w.name}'" for w in signal_workers)
            raise SignalSourceUnresolvedError(
                f'SIGNAL worker(s) {names} have no source. Either give the profile a mock '
                f"'scenario_settings' with a 'data_sentiment_type', or enable a live "
                f'transport in sentiment_config.json.')

        return SignalSourceResolution(
            mode=SignalSourceMode.LIVE,
            worker_count=len(signal_workers),
            reason=(f'{len(signal_workers)} SIGNAL worker(s) have no mounted scenario '
                    f'series — the live {transport.value} transport feeds them'),
            signal_kind=signal_kinds.pop(),
            transport=transport)

    @staticmethod
    def _resolve_transport(
        sentiment_config: SentimentConfig,
    ) -> Optional[SignalTransportKind]:
        """
        Which live transport is enabled, if any.

        There is one, and the interim pull path it replaced is gone: a push connection
        delivers what `/latest` structurally cannot — an envelope superseded between two
        polls was unrecoverable on the pull path — and a heartbeat is what tells a dead
        socket apart from a quiet producer.

        Args:
            sentiment_config: The installation's signal transport settings

        Returns:
            The enabled transport, or None when no live transport is configured
        """
        if sentiment_config.stream.enabled:
            return SignalTransportKind.STREAM
        return None
