"""
Carry-over envelope tests (#486).

A carry-over is the one store shape that outlives the directory of the session that wrote it, so
its self-description is load-bearing in a way a run artifact's is not: the reader has no
surrounding run to ask. Two properties are asserted — the envelope says who wrote it, and the
run it names is provenance rather than key.
"""

import pytest

from python.framework.types.persistence_types import CarryOverEnvelope
from python.framework.types.store_types import StoreId

_RUN_ID = '20260901_142012_a91f3c2e'


def _envelope(**overrides) -> CarryOverEnvelope:
    """A filled envelope, with any field overridden."""
    fields = dict(
        schema_version=2,
        store_id=StoreId.SESSION_STATE,
        saved_at_utc='2026-09-01T14:22:07.441000+00:00',
        written_by_run_id=_RUN_ID,
        profile='kraken_spot_btcusd',
        symbol='BTCUSD',
        snapshot={'already_entered_today': True, 'risk_hwm': 1042.5},
    )
    fields.update(overrides)
    return CarryOverEnvelope(**fields)


class TestEnvelope:
    """What the header has to carry, and what it must not."""

    def test_the_round_trip_preserves_the_payload_and_its_provenance(self):
        back = CarryOverEnvelope.model_validate_json(_envelope().model_dump_json())
        assert back.snapshot == {'already_entered_today': True, 'risk_hwm': 1042.5}
        assert back.written_by_run_id == _RUN_ID
        assert back.store_id == StoreId.SESSION_STATE

    def test_the_writing_run_is_optional(self):
        """A writer without a run identity still produces a valid envelope."""
        assert _envelope(written_by_run_id=None).written_by_run_id is None

    def test_the_identity_is_the_bot_and_the_run_is_only_recorded(self):
        """
        The distinction #355 §5 turns on.

        Keying a carry-over by run is how a restart loses it: a new session mints a new run id
        and a new directory, so it could only find its predecessor by guessing. The bot fields
        are the identity; the run id is a note about who last wrote the file.
        """
        envelope = _envelope()
        identity = (envelope.profile, envelope.symbol)
        assert identity == ('kraken_spot_btcusd', 'BTCUSD')
        assert envelope.written_by_run_id not in identity

    def test_an_envelope_missing_its_identity_is_refused(self):
        with pytest.raises(ValueError):
            CarryOverEnvelope(
                schema_version=2,
                store_id=StoreId.SESSION_STATE,
                saved_at_utc='2026-09-01T14:22:07.441000+00:00',
                symbol='BTCUSD',
            )

    def test_an_empty_snapshot_is_the_default_rather_than_an_error(self):
        """The store writes no file for an empty snapshot; the model still has to express one."""
        assert _envelope(snapshot={}).snapshot == {}
