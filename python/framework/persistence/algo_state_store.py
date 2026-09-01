"""
FiniexTestingIDE - Algo State Store (#354)

Restart-safe persistence for an algo's own internal memory (Category B): regime
flags, counters, risk high-water-marks, "already entered today", swing counters.
A long-running live bot loses all in-memory state on every restart (deploy, crash,
container, power); over a multi-day session a restart is near-certain. This store
snapshots and restores that state across restarts.

Scope is Category B only. It does NOT reconstruct broker positions/orders (Cold-Start
Recovery, #355) and does NOT persist the safety baseline (#356) — both reuse this store.

Live-only by design. The cadence + ALERT-style optional-component shape mirrors the
Reconciler (#151): the tick loop calls is_due() on a hybrid cadence (every N ticks OR
every M wall-clock seconds) and then save(). The store stays decoupled — it knows only
a JSON-serializable dict plus the bot identity, never the decision logic.

Staleness is weekend-aware: a snapshot is measured in TRADING days via the
MarketCalendar (single source of truth) on weekend-closing markets, so a Friday-night
snapshot is not counted as 3 days old on Monday. On 24/7 markets the trading-day count
equals the calendar-day count.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from pydantic import ValidationError

from python.framework.exceptions.persistence_errors import StatePersistenceError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.autotrader_defaults_config_types import (
    StatePersistenceDefaults,
)
from python.framework.types.persistence_types import CarryOverEnvelope, RestoreContext
from python.framework.types.store_types import StoreId
from python.framework.utils.market_calendar import MarketCalendar
from python.framework.utils.time_utils import parse_datetime

# Envelope format version — guards the store's own file format (forward-compat).
# The algo versions its own payload inside `snapshot` if it needs to.
# 2: the shared CarryOverEnvelope (#486) — adds store_id + written_by_run_id provenance.
_SCHEMA_VERSION = 2


class AlgoStateStore:
    """
    Atomic JSON persistence for algo-internal state, keyed by bot identity.

    One state file per running bot (`<profile>_<symbol>.json`) in a stable
    cross-run directory. Writes are atomic (temp file + os.replace) so a crash
    mid-write never leaves a half-written file. Loading applies a corrupt policy
    (file unreadable) and a staleness policy (file too old), both configurable.

    Args:
        config: StatePersistenceDefaults — path, cadence, max-age + policies.
        profile: Bot profile name (part of the file key).
        symbol: Traded symbol (part of the file key).
        weekend_aware: True if the market closes on weekends (Forex) — staleness
            then counts trading days; False for 24/7 markets (crypto).
        logger: Session logger.
        run_id: The writing session's run identity, recorded as PROVENANCE in the
            envelope (#486). Never part of the key — a carry-over is keyed by the bot,
            because a restart mints a new run id and the successor must still find it.
    """

    def __init__(
        self,
        config: StatePersistenceDefaults,
        profile: str,
        symbol: str,
        weekend_aware: bool,
        logger: AbstractLogger,
        run_id: Optional[str] = None,
    ):
        self._config = config
        self._profile = profile
        self._symbol = symbol
        self._weekend_aware = weekend_aware
        self._logger = logger
        self._run_id = run_id

        self._path = Path(config.path) / f'{self._sanitize(profile)}_{self._sanitize(symbol)}.json'

        self._last_save_tick: int = 0
        self._last_save_time: float = time.monotonic()
        self._save_count: int = 0

        self._logger.info(
            f'💾 Algo state store active — file: {self._path} | '
            f'cadence: every {config.save_interval_ticks} ticks or {config.save_interval_seconds}s | '
            f'max_age: {config.max_age_trading_days} trading day(s) '
            f'(weekend_aware={weekend_aware})'
        )

    # ============================================
    # Cadence
    # ============================================

    def is_due(self, tick_counter: int) -> bool:
        """
        Whether a save is due — hybrid cadence (ticks OR wall-clock).

        Args:
            tick_counter: Current tick-loop counter

        Returns:
            True if save_interval_ticks elapsed OR save_interval_seconds elapsed
        """
        if tick_counter - self._last_save_tick >= self._config.save_interval_ticks:
            return True
        return (time.monotonic() - self._last_save_time) >= self._config.save_interval_seconds

    # ============================================
    # Save
    # ============================================

    def save(self, snapshot: Dict[str, Any], tick_counter: int = 0) -> None:
        """
        Persist the algo snapshot atomically. Empty snapshot → no file written.

        Args:
            snapshot: The algo's JSON-serializable internal state
            tick_counter: Current tick-loop counter (updates the cadence tracker)
        """
        # Cadence is consumed regardless of whether anything was written — an
        # empty snapshot still resets the trackers so we don't re-snapshot every
        # tick once due (the per-tick cost would otherwise be the snapshot call).
        self._last_save_tick = tick_counter
        self._last_save_time = time.monotonic()

        # Empty snapshot → nothing to persist (no carcass file). This is the
        # silent-bypass path for algos that opted in but hold no state yet.
        if not snapshot:
            return

        envelope = CarryOverEnvelope(
            schema_version=_SCHEMA_VERSION,
            store_id=StoreId.SESSION_STATE,
            saved_at_utc=datetime.now(timezone.utc).isoformat(),
            written_by_run_id=self._run_id,
            profile=self._profile,
            symbol=self._symbol,
            snapshot=snapshot,
        )

        # Serialize first — a non-serializable value must fail loudly here, not
        # leave a partial file. (The boot pre-flight catches it earlier in dev.)
        try:
            payload = json.dumps(envelope.model_dump(), indent=2)
        except TypeError as e:
            raise StatePersistenceError(
                f'Algo snapshot is not JSON-serializable: {e}. '
                f'Use only JSON primitives (str/int/float/bool/list/dict/None); '
                f'store timestamps as ISO strings.'
            )

        self._atomic_write(payload)
        self._save_count += 1

    # ============================================
    # Load + restore-context
    # ============================================

    def load(self) -> Optional[Tuple[Dict[str, Any], RestoreContext]]:
        """
        Load the persisted snapshot and build its RestoreContext.

        Applies the corrupt policy (unreadable / wrong-schema file) and the
        staleness policy (snapshot older than max_age_trading_days). The caller
        runs the algo-level freshness gate (accepts_restored_state) on the result.

        Returns:
            (snapshot, RestoreContext) to restore, or None to start fresh
        """
        if not self._path.exists():
            return None

        try:
            envelope = CarryOverEnvelope.model_validate_json(self._path.read_bytes())
        except (ValidationError, OSError) as e:
            return self._handle_corrupt(f'unreadable state file: {e}')

        if envelope.schema_version != _SCHEMA_VERSION:
            return self._handle_corrupt(
                f'schema_version mismatch (expected {_SCHEMA_VERSION}, '
                f'got {envelope.schema_version})'
            )

        # Identity mismatch is not corruption — the file simply is not this bot's
        # state. The filename is keyed, so this is a defensive check only.
        if envelope.profile != self._profile or envelope.symbol != self._symbol:
            self._logger.warning(
                f'⚠️ State file identity mismatch '
                f'(file: {envelope.profile}/{envelope.symbol}, '
                f'expected: {self._profile}/{self._symbol}) — ignoring, starting fresh'
            )
            return None

        snapshot = envelope.snapshot
        saved_at = parse_datetime(envelope.saved_at_utc)
        now_utc = datetime.now(timezone.utc)
        age_seconds = max(0.0, (now_utc - saved_at).total_seconds())
        trading_days = self._trading_days_between(saved_at, now_utc)

        # Staleness guard (2.a) — coarse framework net, BEFORE the algo is asked.
        if self._config.max_age_trading_days > 0 and trading_days > self._config.max_age_trading_days:
            return self._handle_stale(saved_at, trading_days)

        ctx = RestoreContext(
            saved_at_utc=saved_at,
            now_utc=now_utc,
            age_seconds=age_seconds,
            trading_days=trading_days,
            weekend_aware=self._weekend_aware,
        )
        self._logger.info(
            f'💾 Loaded algo state — saved {saved_at.isoformat()} '
            f'({trading_days} trading day(s) ago, {len(snapshot)} key(s))'
        )
        return snapshot, ctx

    def shutdown(self) -> None:
        """Emit a final one-line summary to the session log."""
        self._logger.info(
            f'💾 Algo state store final: {self._save_count} save(s) → {self._path}'
        )

    def get_state_path(self) -> Path:
        """
        The resolved state file path for this bot.

        Returns:
            Path to <profile>_<symbol>.json under the configured directory
        """
        return self._path

    # ============================================
    # Internals
    # ============================================

    def _trading_days_between(self, saved_at: datetime, now_utc: datetime) -> int:
        """
        Trading days elapsed between save and now — weekend-aware on Forex.

        Args:
            saved_at: Snapshot timestamp (UTC)
            now_utc: Current time (UTC)

        Returns:
            Trading days elapsed (0 if now precedes saved_at; calendar days on 24/7 markets)
        """
        if now_utc <= saved_at:
            return 0
        if self._weekend_aware:
            # get_trading_days counts inclusive of both endpoints; subtract one
            # so a same-trading-day reload reads as 0 days elapsed.
            return max(0, MarketCalendar.get_trading_days(saved_at, now_utc) - 1)
        return (now_utc - saved_at).days

    def _handle_corrupt(self, reason: str) -> None:
        """
        Apply the on_corrupt policy. Returns None (start fresh) or raises.

        Args:
            reason: Human-readable corruption reason for the log/error
        """
        message = f'Algo state file corrupt — {reason} (file: {self._path})'
        if self._config.on_corrupt == 'fail':
            raise StatePersistenceError(message)
        self._logger.warning(f'⚠️ {message} — starting fresh (on_corrupt=warn_reset)')
        return None

    def _handle_stale(self, saved_at: datetime, trading_days: int) -> None:
        """
        Apply the on_stale policy. Returns None (start fresh) or raises (halt boot).

        The warning is deliberately prominent and multi-line: when an operator
        reopens a bot after days away, they must understand the bot starts with an
        EMPTY memory (counters/flags reset) and that open broker positions are not
        yet recognized (until Cold-Start Recovery, #355).

        Args:
            saved_at: Snapshot timestamp (UTC)
            trading_days: Measured trading-day age
        """
        message = (
            f'Algo state is STALE — saved {saved_at.isoformat()} '
            f'({trading_days} trading day(s) ago > max {self._config.max_age_trading_days})'
        )
        if self._config.on_stale == 'halt':
            raise StatePersistenceError(
                f'{message}. Boot halted (on_stale=halt). '
                f'Delete {self._path} to start fresh, or raise max_age_trading_days.'
            )
        self._logger.warning(
            f'⚠️ {message}.\n'
            f'   → Restored state DISCARDED. The bot starts with EMPTY algo memory: '
            f'counters, entry flags and risk high-water-marks are reset.\n'
            f'   → Open broker positions are NOT recognized yet (until #355) — '
            f'check your account before letting the bot run.'
        )
        return None

    def _atomic_write(self, payload: str) -> None:
        """
        Write payload via temp file + os.replace (atomic, never half-written).

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
