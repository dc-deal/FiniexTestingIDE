"""
Cold-Start Carry-Over — the Store and its Index (#355 Phase 2, #486)

The framework's own carry-over: what the NEXT session needs in order to recognise its
predecessor. Two fields, and each answers something broker truth cannot — which session keys
this bot has sent orders under, and how far its position counter had already run.

Keyed by the BOT, never by the run. That is not a preference: a restart mints a new run id and
a new directory, so a carry-over written under one could only be found by the successor
GUESSING which directory belonged to its predecessor.
"""

import json

from python.framework.persistence.cold_start_state_index import ColdStartStateIndex
from python.framework.persistence.cold_start_state_store import ColdStartStateStore


class TestRoundTrip:
    """What one session writes, the next one reads."""

    def test_a_missing_file_is_a_normal_first_boot(self, store):
        payload = store.load()

        assert payload.session_keys == []
        assert payload.highest_position_counter == 0

    def test_the_key_and_counter_survive(self, store):
        store.save(session_key='1641', highest_position_counter=47)

        payload = store.load()
        assert payload.session_keys == ['1641']
        assert payload.highest_position_counter == 47

    def test_keys_accumulate_across_sessions(self, store):
        # A successor must recognise orders from ANY earlier session, not only the last one —
        # an order can rest for days across several restarts.
        store.save(session_key='1641', highest_position_counter=5)
        store.save(session_key='8b3f', highest_position_counter=9)

        payload = store.load()
        assert payload.session_keys == ['1641', '8b3f']
        assert payload.highest_position_counter == 9

    def test_the_same_key_twice_moves_rather_than_duplicates(self, store):
        store.save(session_key='1641', highest_position_counter=1)
        store.save(session_key='8b3f', highest_position_counter=2)
        store.save(session_key='1641', highest_position_counter=3)

        assert store.load().session_keys == ['8b3f', '1641']

    def test_the_counter_only_ever_rises(self, store):
        # A session that minted nothing must not lower the high-water mark its predecessor left.
        store.save(session_key='1641', highest_position_counter=47)
        store.save(session_key='8b3f', highest_position_counter=0)

        assert store.load().highest_position_counter == 47

    def test_the_key_list_is_capped(self, store):
        for n in range(15):
            store.save(session_key=f'k{n:03d}', highest_position_counter=n)

        keys = store.load().session_keys
        assert len(keys) == 10
        assert keys[-1] == 'k014'          # newest kept
        assert 'k000' not in keys          # oldest dropped

    def test_a_key_an_order_still_needs_is_never_evicted(self, store):
        """
        Relevance beats recency.

        This is the hole the review found: with recency-only eviction, ten restarts after an
        order starts resting drop the key that OWNS it — and the next boot then reads its own
        order as a stranger's, silently, because with nothing attributable left it reports
        "nothing of ours" and trades on.
        """
        store.save(session_key='8b3f', highest_position_counter=1)
        for n in range(15):
            store.save(session_key=f'k{n:03d}', highest_position_counter=n,
                       keys_in_use={'8b3f'})

        keys = store.load().session_keys
        assert '8b3f' in keys, 'the key owning a resting order was evicted'

    def test_without_a_protected_set_the_cap_still_holds(self, store):
        # `keys_in_use=None` means "unknown", which protects nothing — the plain cap applies.
        for n in range(15):
            store.save(session_key=f'k{n:03d}', highest_position_counter=n)

        assert len(store.load().session_keys) == 10

    def test_provenance_is_recorded_and_is_not_the_key(self, store):
        store.save(session_key='1641', highest_position_counter=1)

        raw = json.loads(store.get_state_path().read_text(encoding='utf-8'))
        assert raw['written_by_run_id'] == '20260901_120000_abcdef12'
        # The FILE is named after the bot — a successor with a different run id finds it.
        assert store.get_state_path().name == 'btcusd_test_btcusd.json'
        assert raw['store_id'] == 'cold_start_state'


class TestUnreadableDocument:
    """A damaged carry-over degrades; it does not stop a session."""

    def test_garbage_is_reported_and_treated_as_absent(self, store, logger):
        store.save(session_key='1641', highest_position_counter=1)
        store.get_state_path().write_text('{not json', encoding='utf-8')

        payload = store.load()

        assert payload.session_keys == []
        assert any('unreadable' in w for w in logger.warnings)

    def test_another_bots_document_is_ignored(self, store, logger, tmp_path):
        other = ColdStartStateStore(
            root=tmp_path / 'cold_start_state', profile='btcusd_test', symbol='BTCUSD',
            logger=logger, run_id='r1')
        other.save(session_key='1641', highest_position_counter=1)
        raw = json.loads(store.get_state_path().read_text(encoding='utf-8'))
        raw['symbol'] = 'ETHUSD'
        store.get_state_path().write_text(json.dumps(raw), encoding='utf-8')

        assert store.load().session_keys == []
        assert any('belongs to' in w for w in logger.warnings)


class TestIndexHasAProducer:
    """
    A write refreshes the index — without that it is never built at all.

    Every index in this model has a producer: the run index writes incrementally, the ledger
    rebuilds after appending, the certificate index heals on read. One with no producer reports
    "stale — never built" in `store_cli catalog` forever, which is how this was found.
    """

    def test_saving_builds_the_index(self, store, tmp_path):
        index = ColdStartStateIndex(tmp_path / 'cold_start_state')
        assert index.exists() is False

        store.save(session_key='1641', highest_position_counter=47)

        assert index.exists() is True
        assert index.is_valid() is True
        assert len(index.read()) == 1

    def test_a_second_write_keeps_the_index_current(self, store, tmp_path):
        index = ColdStartStateIndex(tmp_path / 'cold_start_state')
        store.save(session_key='1641', highest_position_counter=1)
        store.save(session_key='1641', highest_position_counter=9)

        assert index.is_valid() is True
        assert int(index.read().loc[0, 'highest_position_counter']) == 9


class TestIndex:
    """The read path across bots — the question an operator asks after a 03:00 restart."""

    def test_it_describes_every_bot(self, store, logger, tmp_path):
        root = tmp_path / 'cold_start_state'
        store.save(session_key='1641', highest_position_counter=47)
        ColdStartStateStore(root=root, profile='ethusd_test', symbol='ETHUSD',
                            logger=logger, run_id='r2').save('8b3f', 3)

        index = ColdStartStateIndex(root)
        assert index.rebuild() == 2

        frame = index.read().sort_values('symbol').reset_index(drop=True)
        assert list(frame['symbol']) == ['BTCUSD', 'ETHUSD']
        assert int(frame.loc[0, 'highest_position_counter']) == 47
        assert int(frame.loc[0, 'session_keys']) == 1

    def test_a_damaged_document_is_described_not_skipped(self, store, logger, tmp_path):
        root = tmp_path / 'cold_start_state'
        store.save(session_key='1641', highest_position_counter=1)
        (root / 'broken_bot.json').write_text('{nope', encoding='utf-8')

        index = ColdStartStateIndex(root)
        # Two documents, two rows. Refusing to describe the healthy bot would fail the read
        # path in exactly the case an operator opens it for — and SKIPPING the broken one
        # would leave the row count below the file count, which is what the staleness rule
        # measures, so the index could never satisfy its own gate.
        assert index.rebuild() == 2
        assert index.is_valid() is True

        frame = index.read().set_index('file')
        assert frame.loc['broken_bot.json', 'status'] == 'unreadable'
        assert frame.loc['btcusd_test_btcusd.json', 'status'] == 'ok'

    def test_a_removed_bot_makes_the_index_stale(self, store, logger, tmp_path):
        # Deletion leaves every surviving file's mtime untouched, so a purely time-based
        # rule would keep reporting a bot that is gone. The row count is what catches it.
        root = tmp_path / 'cold_start_state'
        store.save(session_key='1641', highest_position_counter=1)
        ColdStartStateStore(root=root, profile='ethusd_test', symbol='ETHUSD',
                            logger=logger, run_id='r2').save('8b3f', 3)
        index = ColdStartStateIndex(root)
        index.rebuild()
        assert index.is_valid() is True

        (root / 'ethusd_test_ethusd.json').unlink()

        assert index.is_valid() is False
        assert 'indexed' in index.staleness_reason()

    def test_the_index_is_named_after_its_store(self, tmp_path):
        # Every index in this model is `<store_id>_index.parquet` — a file on disk has to say
        # which store it belongs to, and it is not hidden.
        index = ColdStartStateIndex(tmp_path / 'cold_start_state')
        assert index.get_path().name == 'cold_start_state_index.parquet'
