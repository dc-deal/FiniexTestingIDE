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

import pytest

from python.framework.persistence.cold_start_state_index import ColdStartStateIndex
from python.framework.persistence.cold_start_state_store import ColdStartStateStore
from python.framework.types.persistence_types import (
    AccountDrawdownCarryOver,
    BaselineKind,
    BaselineOrigin,
    BaselineQuantities,
    CarryOverLoadOutcome,
    PositionCarryOver,
    RiskBaseline,
)
from python.framework.types.run_kind_types import OrdersTo


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
        # The FILE is named after the bot — a successor with a different run id finds it. The
        # separator is RESERVED since #538, so the profile's own underscore became a hyphen and
        # the one underscore left is the join: `<profile>_<symbol>`, unambiguously.
        assert store.get_state_path().name == 'btcusd-test_btcusd.json'
        assert raw['store_id'] == 'cold_start_state'

    def test_the_envelope_records_the_identity_its_name_came_from(self, tmp_path, logger):
        """
        Without it the document cannot say what it is filed under, and anything recomputing that
        name from `profile` + `symbol` alone computes a DIFFERENT one and orphans the file — the
        migration script being the first such caller (#538).
        """
        store = ColdStartStateStore(
            root=tmp_path, profile='dotusd_production', symbol='DOTUSD',
            logger=logger, run_id='20260901_120000_abcdef12', bot_id='dot-usd-main')
        store.save(session_key='1641', highest_position_counter=1)

        raw = json.loads(store.get_state_path().read_text(encoding='utf-8'))

        assert store.get_state_path().name == 'dot-usd-main_dotusd.json'
        assert raw['bot_id'] == 'dot-usd-main'
        # The composed name is NOT the file name here — which is the whole point.
        assert raw['profile'] == 'dotusd_production' and raw['symbol'] == 'DOTUSD'


class TestTheRiskBaselineSurvivesTheDisk:
    """
    The fix in #356 is only real if the record reaches the next PROCESS (#356 Phase B).

    Everything else about the baseline is provable in memory: the tracker adopts a restored
    record, keeps its stamp, refuses to re-anchor. None of that matters if the write drops
    the field on the way to disk — the successor then finds nothing, takes a fresh baseline
    at the drawn-down value, and every unit test stays green while the drift is back.

    That failure has happened in this project before, one layer up: a config field declared
    in the model, mirrored in the file, allowed through the key check and read at runtime,
    and silently dropped by the loader that transferred it.
    """

    @staticmethod
    def _baseline() -> RiskBaseline:
        return RiskBaseline(
            kind=BaselineKind.HIGH_WATER_MARK,
            value=10_231.5,   # == 2_047.5 + 0.093 * 88_000, the spot invariant
            taken_at_utc='2026-09-10T08:00:00+00:00',
            origin=BaselineOrigin.FIRST_TICK,
            mark_price=88_000.0,
            quantities=BaselineQuantities(quote=2_047.5, base=0.093),
            exclusive_account=True)

    def test_it_comes_back_field_for_field(self, store):
        store.save(session_key='1641', highest_position_counter=1,
                   risk_baseline=self._baseline())

        restored = store.load().risk_baseline

        assert restored == self._baseline(), (
            'the successor cannot continue a drawdown it cannot read')

    def test_the_stamp_is_not_re_written_on_the_way_through(self, store):
        """A restart does not strike a new denominator, so nothing may re-stamp it."""
        store.save(session_key='1641', highest_position_counter=1,
                   risk_baseline=self._baseline())

        assert store.load().risk_baseline.taken_at_utc == '2026-09-10T08:00:00+00:00'

    def test_a_spot_record_stays_re_derivable_across_the_disk(self, store):
        """
        `value == quote + base * mark_price` — and the point is that it still holds AFTER JSON.

        A denominator nobody can check is a denominator nobody can argue with when a drawdown
        figure looks wrong, so the quantities have to survive the write intact rather than
        being decoration beside a value that was stored on its own.
        """
        store.save(session_key='1641', highest_position_counter=1,
                   risk_baseline=self._baseline())

        restored = store.load().risk_baseline

        assert restored.quantities.quote + restored.quantities.base * restored.mark_price \
            == pytest.approx(restored.value)

    def test_not_supplying_it_leaves_the_stored_one_untouched(self, store):
        """
        The store's `None` convention, on the field that most depends on it.

        The book is written on a STRUCTURAL change of the open positions, far more often
        than the baseline moves. Every one of those writes passes `risk_baseline=None`, so a
        `None` that overwrote would erase the record on the first position that opened.
        """
        store.save(session_key='1641', highest_position_counter=1,
                   risk_baseline=self._baseline())

        store.save(session_key='1641', highest_position_counter=2)

        assert store.load().risk_baseline == self._baseline()

    def test_a_first_boot_has_none_rather_than_a_default(self, store):
        """A zero-valued record would be a denominator nobody took."""
        assert store.load().risk_baseline is None


class TestTheReportedDrawdownSurvivesTheDisk:
    """
    The same proof one reader over (#497).

    The baseline above is what a LIMIT measures against; this is what the REPORT states. Both
    used to die with the process and #356 fixed only the first, so a thirty-day run with
    rehearsed restarts (#476) reported the drawdown of its last segment and said nothing about
    the rest — silently, because a carried figure and a fresh one look identical.

    Asserted at the DISK rather than in memory for the reason the baseline suite states: a
    field can be declared, restored and rendered correctly and still be dropped by the one
    write that carries it, and every in-memory test stays green while the drift is back.
    """

    @staticmethod
    def _drawdown() -> AccountDrawdownCarryOver:
        # A run that peaked at 11 000, fell to 9 500, then recovered: 13.6 % against the peak
        # standing at the time, where the quotient of the two finished figures would say less.
        return AccountDrawdownCarryOver(
            max_equity=12_000.0,
            max_drawdown=1_500.0,
            max_drawdown_pct=13.636363636363637,
            taken_at_utc='2026-09-10T08:00:00+00:00',
            restarts=2,
            curve_started_utc='2026-09-01T06:00:00+00:00')

    def test_it_comes_back_field_for_field(self, store):
        store.save(session_key='1641', highest_position_counter=1,
                   account_drawdown=self._drawdown())

        assert store.load().account_drawdown == self._drawdown(), (
            'the successor cannot continue a curve it cannot read')

    def test_the_percentage_is_carried_rather_than_recomputed(self, store):
        """
        The defect #497 removed, now across a process boundary.

        `max_drawdown / max_equity` is a SMALLER number than the truth on every run that
        recovered — which is every profitable one — because the two floats belong to different
        instants. A successor that stored only the two and re-derived the share would
        reintroduce it at the one point nobody looks.
        """
        store.save(session_key='1641', highest_position_counter=1,
                   account_drawdown=self._drawdown())

        restored = store.load().account_drawdown
        quotient = restored.max_drawdown / restored.max_equity * 100

        assert restored.max_drawdown_pct == pytest.approx(13.6363636)
        assert restored.max_drawdown_pct > quotient, (
            f'the carried share is {restored.max_drawdown_pct:.2f} % and the quotient of the '
            f'two finished figures is {quotient:.2f} % — storing only the floats would ship '
            f'the smaller one')

    def test_not_supplying_it_leaves_the_stored_one_untouched(self, store):
        """
        The store's `None` convention, on the field that would silently lose a month.

        The book is written on every structural change of the open positions. If one of those
        writes erased the curve, the next restart would start at its own opening balance and
        the thirty-day drawdown would describe whatever happened after the last position
        opened.
        """
        store.save(session_key='1641', highest_position_counter=1,
                   account_drawdown=self._drawdown())

        store.save(session_key='1641', highest_position_counter=2)

        assert store.load().account_drawdown == self._drawdown()

    def test_a_first_boot_has_none_rather_than_a_default(self, store):
        """A zero-valued record would claim a peak of zero and never report a decline."""
        assert store.load().account_drawdown is None

    def test_the_curve_start_is_not_the_handover_stamp(self):
        """
        Two stamps, and confusing them understates exactly what the count exists to show.

        `taken_at_utc` is re-written on every carry-over write, so it walks toward the present
        while the deployment grows. `curve_started_utc` is minted once and copied forward — a
        restart does not begin a new curve, the same convention the risk baseline states for
        its own restored record.
        """
        record = self._drawdown()

        assert record.curve_started_utc == '2026-09-01T06:00:00+00:00'
        assert record.taken_at_utc == '2026-09-10T08:00:00+00:00'
        assert record.curve_started_utc < record.taken_at_utc, (
            'the start must be the older of the two, or the line reading "in force since" is '
            'reporting the handover')

    def test_the_deployment_identity_survives_so_the_sessions_can_be_joined(self, store):
        """
        The join key, and it is the one thing here that cannot be reconstructed afterwards.

        Each session writes its own ledger fragment under its own run id; the profile name
        says which BOT, never which deployment — the same profile stopped for a month and
        restarted is a second one. Written while the sessions run, the rows assemble into one
        history; missing, they are N records nobody can attach to each other.
        """
        store.save(session_key='1641', highest_position_counter=1,
                   deployment_id='deploy_20260901_060000')

        store.save(session_key='8b3f', highest_position_counter=2)

        assert store.load().deployment_id == 'deploy_20260901_060000', (
            'the successor minted its own identity and the deployment silently became two')

    def test_the_baseline_and_the_curve_are_two_records(self, store):
        """
        They answer different questions and must not be merged (#497 phase 2).

        The baseline is a denominator a limit compares against; the curve is the account's own
        peak-to-trough history. The peak here is deliberately NOT the baseline's value — if one
        were derived from the other, this is where it would show.
        """
        store.save(session_key='1641', highest_position_counter=1,
                   risk_baseline=TestTheRiskBaselineSurvivesTheDisk._baseline(),
                   account_drawdown=self._drawdown())

        payload = store.load()

        assert payload.risk_baseline.value == pytest.approx(10_231.5)
        assert payload.account_drawdown.max_equity == pytest.approx(12_000.0)



class TestTheReadingsCarryTheirRunKind:
    """
    The two readings say whose orders they followed (#626), and the stamp never lies about them.

    A successor continues them only when its own orders go the same way. That works only if the
    stamp always describes the readings beside it: a session that starts its own replaces the
    carried ones as a whole at its first write, absent ones included, and every other write
    leaves the stamp alone where it leaves the readings alone.
    """

    @staticmethod
    def _both():
        return dict(risk_baseline=TestTheRiskBaselineSurvivesTheDisk._baseline(),
                    account_drawdown=TestTheReportedDrawdownSurvivesTheDisk._drawdown())

    def test_the_stamp_survives_the_disk(self, store):
        store.save(session_key='1641', highest_position_counter=1,
                   readings_orders_to=OrdersTo.VENUE, **self._both())

        assert store.load().readings_orders_to is OrdersTo.VENUE

    def test_a_write_without_readings_leaves_the_stamp_and_the_readings(self, store):
        store.save(session_key='1641', highest_position_counter=1,
                   readings_orders_to=OrdersTo.VENUE, **self._both())
        store.save(session_key='8b3f', highest_position_counter=2)

        payload = store.load()
        assert payload.readings_orders_to is OrdersTo.VENUE
        assert payload.risk_baseline is not None and payload.account_drawdown is not None

    def test_a_replacement_takes_absent_readings_as_absent(self, store):
        """The boot write of a session that starts fresh: nothing of its own yet, nothing kept."""
        store.save(session_key='1641', highest_position_counter=1,
                   readings_orders_to=OrdersTo.SIMULATED, **self._both())
        store.save(session_key='8b3f', highest_position_counter=2,
                   readings_orders_to=OrdersTo.VENUE, replace_readings=True)

        payload = store.load()
        assert payload.risk_baseline is None and payload.account_drawdown is None
        assert payload.readings_orders_to is OrdersTo.VENUE

    def test_a_document_from_before_has_no_stamp(self, store):
        store.save(session_key='1641', highest_position_counter=1, **self._both())

        assert store.load().readings_orders_to is None


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


class TestADeclaredContinuation:
    """
    Two stages of one bot share its document when the later declares `carry_over_from` (#358).

    The later stage reads what the earlier left — the restart stage needs the baseline the soft
    stage took, the hard-restart stage needs the lot the hard stop kept. And the EARLIER stage
    reads what the later left, because the store writes by reading first: a stage that ignored
    the document on read would replace the later stage's book on its first write.
    """

    @staticmethod
    def _stage(tmp_path, logger, profile: str, carry_over_from: str = '') -> ColdStartStateStore:
        """One stage of a bot whose two profiles share `bot_id` 'cert01'."""
        return ColdStartStateStore(
            root=tmp_path / 'cold_start_state', profile=profile, symbol='BTCUSD', logger=logger,
            run_id=f'run_{profile}', bot_id='cert01', carry_over_from=carry_over_from)

    @staticmethod
    def _lot() -> PositionCarryOver:
        """The lot a hard stop kept."""
        return PositionCarryOver(
            position_id='pos_btcusd_1', symbol='BTCUSD', direction='long', lots=6e-05,
            original_lots=6e-05, entry_price=100_000.0, entry_time='2026-10-14T12:00:00+00:00',
            entry_type='market', contract_size=1)

    def test_the_later_stage_reads_the_earlier_one(self, tmp_path, logger):
        baseline = TestTheRiskBaselineSurvivesTheDisk._baseline()
        self._stage(tmp_path, logger, 'cert_soft').save(
            session_key='1641', highest_position_counter=3, risk_baseline=baseline)

        later = self._stage(tmp_path, logger, 'cert_restart', carry_over_from='cert_soft')
        payload = later.load()

        assert payload.risk_baseline == baseline
        load = later.get_last_load()
        assert (load.outcome, load.writer_profile, load.writer_run_id) == (
            CarryOverLoadOutcome.ACCEPTED, 'cert_soft', 'run_cert_soft')

    def test_the_earlier_stage_reads_and_keeps_what_the_later_one_left(self, tmp_path, logger):
        # A rerun of the earlier stage after the later one: its first write must carry the lot
        # on, not replace it with an empty book.
        self._stage(tmp_path, logger, 'cert_restart', carry_over_from='cert_soft').save(
            session_key='8b3f', highest_position_counter=1, open_positions=[self._lot()])

        earlier = self._stage(tmp_path, logger, 'cert_soft')
        assert [p.position_id for p in earlier.load().open_positions] == ['pos_btcusd_1']
        earlier.save(session_key='77aa', highest_position_counter=1)

        assert [p.position_id for p in earlier.load().open_positions] == ['pos_btcusd_1']

    def test_a_third_profile_is_still_another_bot(self, tmp_path, logger):
        self._stage(tmp_path, logger, 'cert_soft').save(
            session_key='1641', highest_position_counter=3, open_positions=[self._lot()])

        copy = self._stage(tmp_path, logger, 'cert_copy')

        assert copy.load().open_positions == []
        load = copy.get_last_load()
        assert (load.outcome, load.writer_profile, load.open_positions, load.session_keys) == (
            CarryOverLoadOutcome.FOREIGN, 'cert_soft', 1, 1)

    def test_the_envelope_records_the_writer_s_declaration(self, tmp_path, logger):
        later = self._stage(tmp_path, logger, 'cert_restart', carry_over_from='cert_soft')
        later.save(session_key='1641', highest_position_counter=1)

        envelope = json.loads(later.get_state_path().read_text(encoding='utf-8'))
        assert envelope['carry_over_from'] == 'cert_soft'


class TestAnotherProfilesClaimsAreNeverReplaced:
    """
    A document another profile wrote is ignored on read — and the store writes by reading
    first, so the next write would replace it. Where it still speaks for the venue, that write
    is how a position stays at the venue with no bot that knows about it: the boot refuses
    before it gets here, and the store holds the line for a second session that appears mid-run.
    The usual cause is a profile renamed while its `bot_id` stayed.
    """

    @staticmethod
    def _bot(tmp_path, logger, profile: str) -> ColdStartStateStore:
        return ColdStartStateStore(
            root=tmp_path / 'cold_start_state', profile=profile, symbol='BTCUSD', logger=logger,
            run_id=f'run_{profile}', bot_id='bot01')

    def test_an_open_book_is_not_overwritten(self, tmp_path, logger):
        lot = TestADeclaredContinuation._lot()
        self._bot(tmp_path, logger, 'old_name').save(
            session_key='1641', highest_position_counter=1, open_positions=[lot])

        renamed = self._bot(tmp_path, logger, 'new_name')
        renamed.save(session_key='8b3f', highest_position_counter=1, open_positions=[])

        envelope = json.loads(renamed.get_state_path().read_text(encoding='utf-8'))
        assert envelope['profile'] == 'old_name'
        assert len(envelope['snapshot']['open_positions']) == 1
        assert any('NOT written' in e for e in logger.errors)

    def test_session_keys_alone_are_a_claim_too(self, tmp_path, logger):
        # A key is how a successor recognises an order resting at the venue under it.
        self._bot(tmp_path, logger, 'old_name').save(session_key='1641', highest_position_counter=1)

        renamed = self._bot(tmp_path, logger, 'new_name')
        renamed.save(session_key='8b3f', highest_position_counter=1)

        envelope = json.loads(renamed.get_state_path().read_text(encoding='utf-8'))
        assert envelope['snapshot']['session_keys'] == ['1641']

    def test_a_document_that_claims_nothing_may_be_started_over(self, tmp_path, logger):
        # What a dry session writes: no key, no book — nothing at the venue depends on it.
        self._bot(tmp_path, logger, 'old_name').save(session_key='', highest_position_counter=0)

        renamed = self._bot(tmp_path, logger, 'new_name')
        renamed.save(session_key='8b3f', highest_position_counter=1)

        envelope = json.loads(renamed.get_state_path().read_text(encoding='utf-8'))
        assert (envelope['profile'], envelope['snapshot']['session_keys']) == ('new_name', ['8b3f'])


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
        assert frame.loc['btcusd-test_btcusd.json', 'status'] == 'ok'

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

        (root / 'ethusd-test_ethusd.json').unlink()

        assert index.is_valid() is False
        assert 'indexed' in index.staleness_reason()

    def test_the_index_is_named_after_its_store(self, tmp_path):
        # Every index in this model is `<store_id>_index.parquet` — a file on disk has to say
        # which store it belongs to, and it is not hidden.
        index = ColdStartStateIndex(tmp_path / 'cold_start_state')
        assert index.get_path().name == 'cold_start_state_index.parquet'
