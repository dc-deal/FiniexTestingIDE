"""
FiniexTestingIDE - Cold-Start State Store (#355)

The FRAMEWORK's carry-over, beside the algo's. One atomic JSON document per bot at
`data/runtime/cold_start_state/<profile>_<symbol>.json`, wrapped in the shared
CarryOverEnvelope (#486).

Its own store rather than a second section inside the algo store (#354), and the reason is
structural rather than tidiness: the algo store is constructed only when the decision logic
declares `uses_state_persistence()`, its own opt-in. This state has to be written for EVERY
live bot — a bot whose algo holds no memory still sends orders under a session key, and its
successor still has to recognise them. Sharing the file would mean taking that gate apart and
putting two writers into one atomic write.

Keyed by the BOT, never by the run: a restart mints a new run id and a new directory, so a
carry-over written under one could only be found by the next session GUESSING which directory
was its predecessor (§44, #355 §5). The writing run is recorded as PROVENANCE in the envelope.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

from pydantic import ValidationError

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.persistence.cold_start_state_index import ColdStartStateIndex
from python.framework.types.persistence_types import (
    CarryOverEnvelope,
    ColdStartPayload,
    PositionCarryOver,
)
from python.framework.types.store_types import StoreId

# Envelope format version for THIS store's documents — independent of the algo store's.
_SCHEMA_VERSION = 1

# How many session discriminators are kept. A key is only useful while an order sent under it
# might still be resting, and an order resting across ten restarts is a case for the operator
# rather than for automatic adoption. Unbounded growth over a thirty-day run is the thing this
# avoids; the number itself is a judgement, and it is stated here rather than hidden.
_MAX_SESSION_KEYS = 10


class ColdStartStateStore:
    """
    Atomic JSON persistence for the framework's cold-start carry-over, keyed by bot identity.

    Args:
        root: Directory holding one document per bot
        profile: Bot profile name (half the identity)
        symbol: Traded symbol (the other half)
        logger: Session logger
        run_id: The writing session's run identity, recorded as PROVENANCE — never part of
            the key, because the successor must find this file without knowing it
    """

    def __init__(
        self,
        root: Path,
        profile: str,
        symbol: str,
        logger: AbstractLogger,
        run_id: Optional[str] = None,
    ):
        self._root = Path(root)
        self._profile = profile
        self._symbol = symbol
        self._logger = logger
        self._run_id = run_id
        self._path = self._root / f'{self._sanitize(profile)}_{self._sanitize(symbol)}.json'
        # Provenance from the last load: WHEN the document was written. Kept as the stamp and
        # never turned into an age — deriving one needs a "now", and at boot the canonical
        # clock is not injected yet (§9).
        self._saved_at: Optional[str] = None

    def get_saved_at(self) -> Optional[str]:
        """
        When the loaded document was written, ISO-8601 UTC.

        Returns:
            The envelope's stamp, or None when nothing was loaded or there was no file
        """
        return self._saved_at

    def get_state_path(self) -> Path:
        """
        The resolved document path for this bot.

        Returns:
            Path to <profile>_<symbol>.json under the configured root
        """
        return self._path

    # ============================================
    # Load
    # ============================================

    def load(self) -> ColdStartPayload:
        """
        Read this bot's carry-over, or an empty payload when there is none.

        A missing file is the normal first-run case and says nothing is wrong. A file that
        cannot be read is reported and treated as absent: refusing to boot over an unreadable
        carry-over would turn a convenience into a single point of failure, and the cost of
        ignoring it is bounded — the successor simply cannot recognise its predecessor's
        orders and reports them as foreign, which is the honest fallback.

        Returns:
            The stored payload, or an empty one
        """
        self._saved_at = None
        if not self._path.exists():
            return ColdStartPayload()

        try:
            envelope = CarryOverEnvelope.model_validate_json(self._path.read_bytes())
        except (ValidationError, OSError) as e:
            self._logger.warning(
                f'⚠️ Cold-start carry-over unreadable ({e}) — continuing without it. '
                f'Orders from an earlier session will read as foreign. File: {self._path}'
            )
            return ColdStartPayload()

        if envelope.schema_version != _SCHEMA_VERSION:
            self._logger.warning(
                f'⚠️ Cold-start carry-over has schema_version {envelope.schema_version} '
                f'(expected {_SCHEMA_VERSION}) — ignored. File: {self._path}'
            )
            return ColdStartPayload()

        if envelope.profile != self._profile or envelope.symbol != self._symbol:
            self._logger.warning(
                f'⚠️ Cold-start carry-over belongs to {envelope.profile}/{envelope.symbol}, '
                f'not {self._profile}/{self._symbol} — ignored'
            )
            return ColdStartPayload()

        self._saved_at = envelope.saved_at_utc

        try:
            return ColdStartPayload.model_validate(envelope.snapshot)
        except ValidationError as e:
            self._logger.warning(
                f'⚠️ Cold-start carry-over payload rejected ({e}) — continuing without it'
            )
            return ColdStartPayload()

    # ============================================
    # Save
    # ============================================

    def save(
        self,
        session_key: str,
        highest_position_counter: int,
        keys_in_use: Optional[Set[str]] = None,
        open_positions: Optional[List[PositionCarryOver]] = None,
        refresh_index: bool = True,
    ) -> None:
        """
        Record this session's key, counter high-water mark and open book for the next session.

        Read-modify-write: the stored keys are extended rather than replaced, because the point
        of the list is that a successor recognises orders from ANY earlier session, not only the
        last one. The current session's key moves to the end (newest last).

        **Eviction is by relevance first, recency second.** A key whose order is still resting
        at the venue is never dropped, however old it is. Recency alone was a hole with teeth:
        ten restarts after an order started resting, the key that owns it would age out, and
        the next session would read its own order as a stranger's — silently, because with
        nothing attributable left the boot reports "nothing of ours" and trades on. Which is
        the opposite of what this store exists for.

        Args:
            session_key: This session's client-order-id discriminator ('' when none is stamped)
            highest_position_counter: The largest position counter minted this session
            keys_in_use: Session halves the venue currently shows on orders of our shape.
                Protected from eviction. None means "unknown", which protects nothing
            open_positions: The open book at this moment. None means "not supplied" and leaves
                the stored book untouched — a caller that only advances the key must not
                silently erase what the bot holds. An empty LIST means "the book is empty",
                which is a statement and does overwrite
            refresh_index: Whether to rebuild the store index afterwards. False for the
                writes that happen DURING a session: the rebuild reads every bot's document
                and costs 26-40 ms on this project's tree (§42), which has no business inside
                a tick loop — and the index answers a cross-BOT question ("which bot carries
                what") that nobody asks mid-session. It is derived and disposable (§44), so a
                session that ends without one leaves a store that reports itself stale and
                rebuilds on the next boot or via `store_cli.py rebuild cold_start_state`
        """
        payload = self.load()
        protected = set(keys_in_use or ())

        keys = [k for k in payload.session_keys if k and k != session_key]
        if session_key:
            keys.append(session_key)

        # Protected keys always survive; the newest of the rest fill the remaining room. If the
        # protected set alone exceeds the cap the list grows past it — deliberately: dropping a
        # key an order still depends on is the failure the cap was never worth.
        droppable = [k for k in keys if k not in protected]
        room = max(0, _MAX_SESSION_KEYS - (len(keys) - len(droppable)))
        kept = set(droppable[-room:]) if room else set()
        payload.session_keys = [k for k in keys if k in protected or k in kept]
        payload.highest_position_counter = max(
            payload.highest_position_counter, highest_position_counter)
        if open_positions is not None:
            payload.open_positions = list(open_positions)

        envelope = CarryOverEnvelope(
            schema_version=_SCHEMA_VERSION,
            store_id=StoreId.COLD_START_STATE,
            # Wall-clock, and legitimately so (§9): this stamps when WE wrote the file — an
            # observation of our own act, not an event time, and nothing decides on it. The
            # store deliberately has no staleness policy: a resting order does not expire
            # because a week passed.
            saved_at_utc=datetime.now(timezone.utc).isoformat(),
            written_by_run_id=self._run_id,
            profile=self._profile,
            symbol=self._symbol,
            snapshot=payload.model_dump(),
        )
        self._atomic_write(json.dumps(envelope.model_dump(), indent=2))
        if refresh_index:
            self._refresh_index()

    def _refresh_index(self) -> None:
        """
        Rebuild the store's index after a write.

        Every index in this model has a producer, and without one it is never built at all —
        `store_cli catalog` reports it stale forever. The cost is bounded by construction: this
        store is written at most twice per session and holds one small document per bot, so a
        full rebuild is cheaper than the bookkeeping an incremental update would need.

        A failure is logged and swallowed: the index is DERIVED and disposable, and losing a
        read path must never cost the carry-over the write it just made.
        """
        try:
            ColdStartStateIndex(self._root).rebuild()
        except Exception as e:
            self._logger.warning(
                f'⚠️ Cold-start index rebuild failed ({e}) — the carry-over itself is written; '
                f'rebuild the index with `store_cli.py rebuild cold_start_state`'
            )

    # ============================================
    # Internals
    # ============================================

    def _atomic_write(self, payload: str) -> None:
        """
        Write via temp file + os.replace, so a crash mid-write never leaves half a document.

        Args:
            payload: Serialized JSON envelope
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_name(self._path.name + '.tmp')
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(payload)
        os.replace(tmp_path, self._path)

    @staticmethod
    def _sanitize(name: str) -> str:
        """
        Reduce an identity component to a safe filename token.

        Args:
            name: Raw profile or symbol string

        Returns:
            Lowercased token with non-alphanumerics collapsed to underscores
        """
        return ''.join(c if c.isalnum() else '_' for c in name).strip('_').lower()
