"""Signal types, JSONL loader, and provider lookup tests (#141)."""

import pytest

from conftest import FIXTURE_JSONL, SYMBOL, error_snapshot, make_provider, snapshot, utc
from python.framework.exceptions.signal_data_errors import SignalSchemaError
from python.framework.signal_data.signal_jsonl_loader import load_signal_series
from python.framework.types.signal_data_types import SignalSnapshot


class TestSignalTypes:
    """Pydantic parse of the archived line (extra-tolerant)."""

    def test_parse_line_with_extra_metadata_ignored(self):
        line = FIXTURE_JSONL.read_text().splitlines()[0]
        snap = SignalSnapshot.model_validate_json(line)
        # collected_msc is the merge key — aware UTC
        assert snap.collected_msc == utc(2026, 1, 15, 8, 0)
        assert snap.collected_msc.tzinfo is not None
        assert snap.result[0].symbol == 'BTCUSD'
        # metadata carries an undeclared field (sources_configured) → tolerated
        assert snap.schema_version == '1.0'

    def test_per_symbol_fields(self):
        line = FIXTURE_JSONL.read_text().splitlines()[1]
        result = SignalSnapshot.model_validate_json(line).result[0]
        assert result.signal == 'BUY'
        assert result.sentiment_score == 0.35
        assert result.confidence == 0.80

    def test_int_ms_collected_msc_is_normalized(self):
        # Wire format is epoch-ms (int); the reader normalizes to a UTC datetime.
        line = '{"collected_msc":1777248000000,"schema_version":"1.0","status":"success","result":[]}'
        snap = SignalSnapshot.model_validate_json(line)
        assert snap.collected_msc == utc(2026, 4, 27, 0, 0)
        assert snap.collected_msc.tzinfo is not None


class TestLoader:
    """JSONL → time-ordered SignalSeries, with schema gate + range trim."""

    def test_loads_and_sorts(self):
        series = load_signal_series(FIXTURE_JSONL, source='llm_sentiment')
        assert len(series.snapshots) == 5
        msc = [s.collected_msc for s in series.snapshots]
        assert msc == sorted(msc)

    def test_error_status_line_kept_with_empty_result(self):
        series = load_signal_series(FIXTURE_JSONL, source='llm_sentiment')
        errors = [s for s in series.snapshots if s.status == 'error']
        assert len(errors) == 1
        assert errors[0].result == []
        assert errors[0].errors[0].type == 'LLM_TIMEOUT'

    def test_schema_mismatch_raises(self, tmp_path):
        bad = tmp_path / 'bad.jsonl'
        bad.write_text(
            '{"collected_msc":"2026-01-15T08:00:00Z","schema_version":"2.0",'
            '"status":"success","result":[]}\n'
        )
        with pytest.raises(SignalSchemaError, match='schema_version'):
            load_signal_series(bad, source='llm_sentiment')

    def test_range_keeps_pre_start_snapshot(self):
        # start 08:15 → keep the 08:10 snapshot (last <= start) onward; drop 08:30 (> end)
        series = load_signal_series(
            FIXTURE_JSONL, source='llm_sentiment',
            start=utc(2026, 1, 15, 8, 15), end=utc(2026, 1, 15, 8, 25))
        msc = [s.collected_msc for s in series.snapshots]
        assert utc(2026, 1, 15, 8, 10) in msc
        assert utc(2026, 1, 15, 8, 30) not in msc


class TestProvider:
    """Nearest collected_msc <= tick (the no-look-ahead anchor)."""

    def test_gap_before_first_snapshot(self):
        provider = make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5))
        assert provider.nearest(utc(2026, 1, 15, 7, 0), SYMBOL) is None

    def test_resolves_most_recent_le_tick(self):
        provider = make_provider(
            snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5, signal='HOLD'),
            snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8, signal='BUY'),
        )
        resolved = provider.nearest(utc(2026, 1, 15, 8, 12), SYMBOL)
        assert resolved.collected_msc == utc(2026, 1, 15, 8, 10)
        assert resolved.result.signal == 'BUY'

    def test_exact_boundary_inclusive(self):
        provider = make_provider(snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8))
        assert provider.nearest(utc(2026, 1, 15, 8, 10), SYMBOL) is not None

    def test_missing_symbol_defensive_hold(self):
        provider = make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5, symbol='ETHUSD'))
        resolved = provider.nearest(utc(2026, 1, 15, 8, 5), 'BTCUSD')
        assert resolved.result.signal == 'HOLD'

    def test_error_snapshot_yields_defensive_hold(self):
        # status='error' → empty result → "snapshot present, no usable sentiment"
        provider = make_provider(error_snapshot(utc(2026, 1, 15, 8, 40)))
        resolved = provider.nearest(utc(2026, 1, 15, 8, 45), SYMBOL)
        assert resolved is not None
        assert resolved.result.signal == 'HOLD'
        assert resolved.result.confidence == 0.0
