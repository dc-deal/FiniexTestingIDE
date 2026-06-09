"""
Algo State Store — persistence, cadence, corrupt + staleness policies (#354)

The store is the framework side of restart-safe algo memory (Category B): atomic
JSON writes keyed by bot identity, a hybrid save cadence, and load-time policies
for corrupt (unreadable) and stale (too-old) state. Staleness is weekend-aware via
the MarketCalendar so a Friday-night snapshot is not 3 days old on Monday.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from python.framework.exceptions.persistence_errors import StatePersistenceError
from python.framework.persistence.algo_state_store import AlgoStateStore


def _make_store(config, logger, weekend_aware=False, profile='btcusd_test', symbol='BTCUSD'):
    """Build a store for the given config (crypto/24-7 by default)."""
    return AlgoStateStore(
        config=config,
        profile=profile,
        symbol=symbol,
        weekend_aware=weekend_aware,
        logger=logger,
    )


class TestRoundTrip:
    """save → load returns the same snapshot plus a populated RestoreContext."""

    def test_save_then_load(self, store_config, logger):
        store = _make_store(store_config, logger)
        snapshot = {'swing_count': 2, 'entered_today': True, 'hwm': 1.2345}
        store.save(snapshot, tick_counter=5)

        loaded = store.load()
        assert loaded is not None
        restored, ctx = loaded
        assert restored == snapshot
        assert ctx.age_seconds >= 0.0
        assert ctx.weekend_aware is False

    def test_load_missing_file_returns_none(self, store_config, logger):
        store = _make_store(store_config, logger)
        assert store.load() is None


class TestEmptySnapshot:
    """An empty snapshot writes no file (silent-bypass — no carcass)."""

    def test_empty_snapshot_writes_no_file(self, store_config, logger):
        store = _make_store(store_config, logger)
        store.save({}, tick_counter=1)
        assert not store.get_state_path().exists()
        assert store.load() is None


class TestAtomicWrite:
    """Writes are atomic — no temp file lingers and the result is valid JSON."""

    def test_no_temp_file_after_save(self, store_config, logger):
        store = _make_store(store_config, logger)
        store.save({'k': 1}, tick_counter=1)
        path = store.get_state_path()
        assert path.exists()
        assert not path.with_name(path.name + '.tmp').exists()
        envelope = json.loads(path.read_text(encoding='utf-8'))
        assert envelope['schema_version'] == 1
        assert envelope['snapshot'] == {'k': 1}


class TestCadence:
    """Hybrid is_due — tick threshold or time threshold."""

    def test_is_due_by_ticks(self, store_config, logger):
        store = _make_store(store_config, logger)  # save_interval_ticks=10
        assert store.is_due(10) is True
        store.save({'k': 1}, tick_counter=10)
        assert store.is_due(15) is False
        assert store.is_due(20) is True


class TestCorruptPolicy:
    """A corrupt state file follows on_corrupt: warn_reset (None) or fail (raise)."""

    def test_warn_reset_starts_fresh(self, store_config, logger):
        store = _make_store(store_config, logger)
        store.save({'k': 1}, tick_counter=1)
        store.get_state_path().write_text('{ this is not json', encoding='utf-8')

        assert store.load() is None
        assert any('corrupt' in w.lower() for w in logger.warnings)

    def test_fail_raises(self, store_config, logger):
        store_config.on_corrupt = 'fail'
        store = _make_store(store_config, logger)
        store.save({'k': 1}, tick_counter=1)
        store.get_state_path().write_text('{ broken', encoding='utf-8')

        with pytest.raises(StatePersistenceError):
            store.load()


class TestStalePolicy:
    """A too-old state file follows on_stale: warn_reset (None) or halt (raise)."""

    def _write_old_file(self, store, days_ago):
        """Save a snapshot, then backdate its saved_at_utc by days_ago calendar days."""
        store.save({'k': 1}, tick_counter=1)
        path = store.get_state_path()
        envelope = json.loads(path.read_text(encoding='utf-8'))
        old = datetime.now(timezone.utc) - timedelta(days=days_ago)
        envelope['saved_at_utc'] = old.isoformat()
        path.write_text(json.dumps(envelope), encoding='utf-8')

    def test_warn_reset_discards(self, store_config, logger):
        store = _make_store(store_config, logger)  # max_age_trading_days=5
        self._write_old_file(store, days_ago=30)

        assert store.load() is None
        assert any('stale' in w.lower() for w in logger.warnings)

    def test_halt_raises(self, store_config, logger):
        store_config.on_stale = 'halt'
        store = _make_store(store_config, logger)
        self._write_old_file(store, days_ago=30)

        with pytest.raises(StatePersistenceError):
            store.load()

    def test_fresh_file_within_age_loads(self, store_config, logger):
        store = _make_store(store_config, logger)
        self._write_old_file(store, days_ago=1)  # 1 calendar day < 5
        loaded = store.load()
        assert loaded is not None


class TestIdentityMismatch:
    """A file from a different profile/symbol is ignored, not treated as corrupt."""

    def test_mismatch_ignored(self, store_config, logger):
        store = _make_store(store_config, logger, profile='btcusd_test', symbol='BTCUSD')
        store.save({'k': 1}, tick_counter=1)
        path = store.get_state_path()
        envelope = json.loads(path.read_text(encoding='utf-8'))
        envelope['symbol'] = 'ETHUSD'  # different bot's state
        path.write_text(json.dumps(envelope), encoding='utf-8')

        assert store.load() is None
        assert any('mismatch' in w.lower() for w in logger.warnings)


class TestWeekendAware:
    """trading_days is weekend-aware on Forex, calendar-day on 24/7 markets."""

    def _backdate_to_friday(self, store):
        """Save then set saved_at_utc to Fri 2026-06-05 12:00 UTC."""
        store.save({'k': 1}, tick_counter=1)
        path = store.get_state_path()
        envelope = json.loads(path.read_text(encoding='utf-8'))
        envelope['saved_at_utc'] = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc).isoformat()
        path.write_text(json.dumps(envelope), encoding='utf-8')

    def _freeze_now_monday(self, monkeypatch):
        """Freeze the store's view of 'now' to Mon 2026-06-08 12:00 UTC."""
        class _Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            'python.framework.persistence.algo_state_store.datetime', _Frozen)

    def test_forex_skips_weekend(self, store_config, logger, monkeypatch):
        store = _make_store(store_config, logger, weekend_aware=True)
        self._backdate_to_friday(store)
        self._freeze_now_monday(monkeypatch)

        loaded = store.load()
        assert loaded is not None
        _, ctx = loaded
        assert ctx.trading_days == 1  # Fri → Mon = 1 trading day (Sat/Sun skipped)

    def test_crypto_counts_calendar_days(self, store_config, logger, monkeypatch):
        store = _make_store(store_config, logger, weekend_aware=False)
        self._backdate_to_friday(store)
        self._freeze_now_monday(monkeypatch)

        loaded = store.load()
        assert loaded is not None
        _, ctx = loaded
        assert ctx.trading_days == 3  # Fri → Mon = 3 calendar days
