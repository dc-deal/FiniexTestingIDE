"""
What a venue itself records, read from Kraken (#512, #127) — against recorded answers in the shapes
measured on 2026-11-09, with invented values; no test reaches the network or a real key.

The client signs a private request the way Kraken's documentation computes its example and keeps
its nonce strictly rising. The public trade list pages forward, joins its pages by trade id and
keeps a stretch's edges. The ledger pages by offset, names the order behind each trade and speaks
our currency codes. A failed read raises instead of answering nothing, and the seam hands a reader
out by broker type, or none.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import pytest
import requests

import python.adapters.kraken.kraken_api_client as kraken_api_client
from python.adapters.kraken.kraken_venue_ledger_reader import (
    KRAKEN_VENUE_LEDGER_KINDS,
    KrakenVenueLedgerReader,
)
from python.adapters.kraken.kraken_api_client import KrakenApiClient
from python.adapters.kraken.kraken_public_trade_reader import KrakenPublicTradeReader
from python.framework.exceptions.connection_errors import ConnectionGaveUpError
from python.framework.factory.venue_history_reader_factory import (
    venue_ledger_reader,
    public_trade_reader,
)
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.venue_history_types import VenueLedgerEntryKind
from tests.shared.null_logger import NullLogger

_POLICY = ConnectionPolicy(initial_delay_s=0.0, jitter=False, attempt_budget=3)
_T0 = datetime(2026, 11, 25, 9, tzinfo=timezone.utc)


def _seconds(minutes: float) -> float:
    return (_T0 + timedelta(minutes=minutes)).timestamp()


class _Answer:
    """One HTTP answer as `requests` hands it over."""

    def __init__(self, body: Dict, status: int = 200):
        self._body = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f'{self.status_code}', response=self)

    def json(self) -> Dict:
        return self._body


class _Venue:
    """Kraken standing in: answers in order per endpoint, and every request kept."""

    def __init__(self, answers: Dict[str, List[_Answer]]):
        self._answers = {endpoint: list(queue) for endpoint, queue in answers.items()}
        self.requests: List[Dict] = []

    def get(self, url, params, timeout):
        return self._answer(url, dict(params))

    def post(self, url, headers, data, timeout):
        return self._answer(url, dict(data), headers)

    def _answer(self, url, sent, headers=None):
        endpoint = url.split('api.kraken.com')[1]
        self.requests.append({'endpoint': endpoint, 'sent': sent, 'headers': headers})
        return self._answers[endpoint].pop(0)


def _client(venue: _Venue, credentials: bool = False) -> KrakenApiClient:
    return KrakenApiClient(15.0,
                           credentials_file='venues/my_read_key.json' if credentials else None,
                           get=venue.get, post=venue.post)


@pytest.fixture
def read_key(tmp_path, monkeypatch):
    """A key of invented values in place of the configured file — never the operator's."""
    path = tmp_path / 'my_read_key.json'
    path.write_text(json.dumps({'api_key': 'my_key', 'api_secret': 'c2VjcmV0'}), encoding='utf-8')
    monkeypatch.setattr(kraken_api_client, 'resolve_credentials_file', lambda name: path)
    return path


def _ok(result: Dict) -> _Answer:
    return _Answer({'error': [], 'result': result})


def _row(trade_id: int, minutes: float) -> list:
    return ['3000.10', '0.01000000', _seconds(minutes), 'b', 'l', '', trade_id]


class TestTheClient:

    def test_a_private_request_is_signed_as_krakens_documented_example(self):
        payload = {'nonce': '1616492376594', 'ordertype': 'limit', 'pair': 'XBTUSD',
                   'price': 37500, 'type': 'buy', 'volume': 1.25}
        secret = ('kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj'
                  '6F1huXg==')
        headers = KrakenApiClient._signed_headers('/0/private/AddOrder', payload, 'my_key', secret)
        assert headers == {'API-Key': 'my_key', 'API-Sign': (
            '4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32b'
            'Ab0nmbRn6H8ndwLUQ==')}

    def test_its_nonce_rises_strictly_within_one_millisecond(self, read_key):
        venue = _Venue({'/0/private/Ledgers': [_ok({}), _ok({})]})
        client = _client(venue, credentials=True)
        client.fetch_private('/0/private/Ledgers', {})
        client.fetch_private('/0/private/Ledgers', {})
        first, second = (int(request['sent']['nonce']) for request in venue.requests)
        assert second > first

    def test_a_public_client_refuses_a_private_read(self):
        with pytest.raises(ValueError, match='public data only'):
            _client(_Venue({})).fetch_private('/0/private/Ledgers', {})

    def test_an_empty_key_is_refused_before_anything_is_sent(self, read_key):
        read_key.write_text(json.dumps({'api_key': '', 'api_secret': ''}), encoding='utf-8')
        venue = _Venue({})
        with pytest.raises(ValueError, match='are empty'):
            _client(venue, credentials=True).fetch_private('/0/private/Ledgers', {})
        assert venue.requests == []


class TestThePublicTradeList:

    def _reader(self, venue: _Venue, waits: List[float]) -> KrakenPublicTradeReader:
        return KrakenPublicTradeReader(_client(venue), _POLICY, NullLogger(), wait=waits.append)

    def test_pages_join_by_id_and_the_stretch_keeps_its_edges(self):
        last_of_first = str(int(_seconds(2) * 1e9))
        venue = _Venue({'/0/public/Trades': [
            _ok({'XETHZUSD': [_row(9, -1), _row(10, 0), _row(11, 1), _row(12, 2)],
                 'last': last_of_first}),
            _ok({'XETHZUSD': [_row(12, 2), _row(13, 4), _row(14, 6)], 'last': '0'}),
        ]})
        waits: List[float] = []
        trades = self._reader(venue, waits).read_trades('ETHUSD', _T0, _T0 + timedelta(minutes=5))
        assert [trade.trade_id for trade in trades] == [10, 11, 12, 13]
        assert waits == [1.0]
        first, second = (request['sent'] for request in venue.requests)
        assert first['pair'] == 'ETHUSD' and first['count'] == 1000
        assert int(first['since']) == int(_T0.timestamp()) * 1_000_000_000 - 1000
        assert second['since'] == last_of_first

    def test_our_symbol_is_named_by_krakens_pair(self):
        venue = _Venue({'/0/public/Trades': [_ok({'XXBTZUSD': [], 'last': '0'})]})
        self._reader(venue, []).read_trades('BTCUSD', _T0, _T0 + timedelta(minutes=1))
        assert venue.requests[0]['sent']['pair'] == 'XBTUSD'

    def test_a_venue_not_answering_now_is_asked_again(self):
        venue = _Venue({'/0/public/Trades': [
            _Answer({'error': ['EService:Unavailable']}),
            _ok({'XETHZUSD': [_row(10, 0)], 'last': str(int(_seconds(0) * 1e9))}),
            _ok({'XETHZUSD': [], 'last': str(int(_seconds(0) * 1e9))}),
        ]})
        trades = self._reader(venue, []).read_trades('ETHUSD', _T0, _T0 + timedelta(minutes=1))
        assert [trade.trade_id for trade in trades] == [10]

    def test_a_refusal_raises_never_an_empty_list(self):
        venue = _Venue({'/0/public/Trades': [_Answer({'error': ['EQuery:Unknown asset pair']})]})
        with pytest.raises(ConnectionGaveUpError):
            self._reader(venue, []).read_trades('ETHUSD', _T0, _T0 + timedelta(minutes=1))


def _entry(refid: str, minutes: float, kind: str, asset: str, amount: str,
           fee: str = '0.0000') -> Dict:
    return {'refid': refid, 'time': _seconds(minutes), 'type': kind,
            'subtype': 'tradespot' if kind == 'trade' else '', 'aclass': 'currency',
            'asset': asset, 'amount': amount, 'fee': fee, 'balance': '1000.0000'}


class TestTheLedger:

    def _reader(self, venue: _Venue, waits: List[float]) -> KrakenVenueLedgerReader:
        return KrakenVenueLedgerReader(_client(venue, credentials=True), _POLICY, NullLogger(),
                                         wait=waits.append)

    def test_pages_and_the_order_behind_each_trade(self, read_key):
        venue = _Venue({
            '/0/private/Ledgers': [
                _ok({'ledger': {'L-3': _entry('T-B', 3, 'trade', 'ZUSD', '-150.0000', '0.1200'),
                                'L-2': _entry('T-B', 3, 'trade', 'XETH', '0.0500000000')},
                     'count': 4}),
                _ok({'ledger': {'L-2': _entry('T-B', 3, 'trade', 'XETH', '0.0500000000'),
                                'L-1': _entry('D-1', 1, 'deposit', 'ZUSD', '500.0000'),
                                'L-0': _entry('D-0', -10, 'deposit', 'ZUSD', '1.0000')},
                     'count': 4}),
            ],
            '/0/private/QueryTrades': [_ok({'T-B': {'ordertxid': 'O-BOT-1', 'pair': 'XETHZUSD'}})],
        })
        waits: List[float] = []
        entries = self._reader(venue, waits).read_ledger(_T0, _T0 + timedelta(minutes=10))
        assert [(entry.entry_id, entry.kind, entry.asset, entry.order_ref)
                for entry in entries] == [
            ('L-1', VenueLedgerEntryKind.DEPOSIT, 'USD', None),
            ('L-2', VenueLedgerEntryKind.TRADE, 'ETH', 'O-BOT-1'),
            ('L-3', VenueLedgerEntryKind.TRADE, 'USD', 'O-BOT-1')]
        assert entries[2].get_balance_change() == pytest.approx(-150.12)
        assert waits == [3.0, 3.0]
        ledgers = [request['sent'] for request in venue.requests
                   if request['endpoint'] == '/0/private/Ledgers']
        assert [page['ofs'] for page in ledgers] == [0, 2]
        assert ledgers[0]['start'] == int(_T0.timestamp()) - 1

    def test_trades_are_named_twenty_at_a_time(self, read_key):
        ledger = {f'L-{n}': _entry(f'T-{n:02d}', 1, 'trade', 'ZUSD', '-1.0000') for n in range(21)}
        venue = _Venue({
            '/0/private/Ledgers': [_ok({'ledger': ledger, 'count': 21})],
            '/0/private/QueryTrades': [_ok({}), _ok({})],
        })
        entries = self._reader(venue, []).read_ledger(_T0, _T0 + timedelta(minutes=10))
        named = [request['sent']['txid'].split(',') for request in venue.requests
                 if request['endpoint'] == '/0/private/QueryTrades']
        assert [len(batch) for batch in named] == [20, 1]
        assert all(entry.order_ref is None for entry in entries)

    def test_a_key_without_the_right_raises(self, read_key):
        venue = _Venue({'/0/private/Ledgers': [_Answer({'error': ['EGeneral:Permission denied']})]})
        with pytest.raises(ConnectionGaveUpError):
            self._reader(venue, []).read_ledger(_T0, _T0 + timedelta(minutes=10))


class TestTheKindsOfEntry:

    def test_every_kind_but_other_has_a_kraken_type(self):
        named = set(KRAKEN_VENUE_LEDGER_KINDS.values())
        assert named == set(VenueLedgerEntryKind) - {VenueLedgerEntryKind.OTHER}

    def test_only_a_trade_can_belong_to_an_order(self):
        assert [kind for kind in VenueLedgerEntryKind if kind.may_belong_to_an_order()] == [
            VenueLedgerEntryKind.TRADE]


class TestTheSeam:

    def test_kraken_has_both_readers(self):
        assert isinstance(public_trade_reader('kraken_spot', _POLICY, NullLogger()),
                          KrakenPublicTradeReader)
        assert isinstance(venue_ledger_reader('kraken_spot', 'venues/my_read_key.json', _POLICY,
                                                NullLogger()), KrakenVenueLedgerReader)

    def test_a_forex_broker_has_none(self):
        assert public_trade_reader('mt5', _POLICY, NullLogger()) is None
        assert venue_ledger_reader('mt5', 'venues/my_read_key.json', _POLICY, NullLogger()) is None

    def test_an_unknown_broker_type_is_refused(self):
        with pytest.raises(ValueError):
            public_trade_reader('my_broker', _POLICY, NullLogger())
