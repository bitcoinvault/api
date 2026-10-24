from copy import deepcopy
from unittest.mock import Mock

import pytest
import requests

from blockchain import rpc
from db import db, query_cache
from db.models import Address, Block, CacheState, UTXO
from test_daemon_cache import daemon


def transaction(txid, inputs, addresses):
    return {'txid': txid, 'hash': txid, 'vin': inputs,
            'vout': [{'n': 0, 'value': 2, 'scriptPubKey': {
                'addresses': addresses, 'hex': '51', 'type': 'nonstandard',
            }}]}


def block(height, transactions):
    return {'hash': 'block-' + str(height), 'height': height,
            'previousblockhash': 'block-' + str(height - 1),
            'time': height, 'difficulty': 1, 'tx': transactions}


def test_spending_an_intentionally_untracked_output_completes_ingestion(daemon, monkeypatch):
    origin = transaction('untracked', [{'coinbase': '00'}], [])
    previous = Mock(return_value=deepcopy(origin))
    db.insert_block(block(11, [origin]), {}, {})
    daemon.update_analyzer()
    monkeypatch.setattr(db, 'get_raw_transaction', previous, raising=False)
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 12)
    monkeypatch.setattr(daemon, 'get_block', lambda height: block(height, [
        transaction('spend', [{'txid': 'untracked', 'vout': 0}], ['recipient']),
    ]))

    daemon.update_blockchain()

    assert query_cache.get_generation() not in (None, 'initial')
    assert Block.objects.get(hash='block-12').height == 12
    assert Address.objects.get(hash='recipient').balance == 2
    assert Address.objects.get(hash='address-one').balance == 7
    assert UTXO.objects(id='untracked0').first() is None
    previous.assert_called_once_with('untracked')


def test_missing_tracked_output_is_still_an_error(daemon, monkeypatch):
    previous = transaction('missing', [{'coinbase': '00'}], ['address-one'])
    monkeypatch.setattr(db, 'get_raw_transaction', Mock(return_value=previous), raising=False)
    with pytest.raises(UTXO.DoesNotExist):
        db.insert_block(block(11, [transaction(
            'spend', [{'txid': 'missing', 'vout': 0}], ['recipient'])]), {}, {})
    assert Block.objects(hash='block-11').first() is None


@pytest.mark.parametrize('previous', [
    {'txid': 'wrong', 'vout': []},
    {'txid': 'missing', 'vout': []},
    {'txid': 'missing', 'vout': [{'n': 0, 'scriptPubKey': {}}]},
    {'txid': 'missing', 'vout': [{'n': 0, 'scriptPubKey': {
        'hex': '51', 'type': 'nonstandard', 'address': 'single-address',
    }}]},
])
def test_unproven_missing_output_is_not_ignored(daemon, monkeypatch, previous):
    monkeypatch.setattr(db, 'get_raw_transaction', Mock(return_value=previous), raising=False)
    with pytest.raises((RuntimeError, UTXO.DoesNotExist)):
        db.insert_block(block(11, [transaction(
            'spend', [{'txid': 'missing', 'vout': 0}], ['recipient'])]), {}, {})
    assert Block.objects(hash='block-11').first() is None


@pytest.mark.parametrize('payload', [
    {'result': None, 'error': {'code': -28, 'message': 'warming up'}},
    {'result': None, 'error': None},
    {'error': None},
    [],
])
def test_rpc_errors_and_malformed_responses_are_not_success(monkeypatch, payload):
    response = Mock()
    response.json.return_value = payload
    monkeypatch.setattr(rpc.requests, 'post', Mock(return_value=response))
    with pytest.raises(RuntimeError, match='RPC'):
        rpc.RPC('getblockhash', [10]).execute()


def test_rpc_timeout_is_bounded_and_classified(monkeypatch):
    post = Mock(side_effect=requests.Timeout('transport details'))
    monkeypatch.setattr(rpc.requests, 'post', post)
    with pytest.raises(RuntimeError, match='RPC'):
        rpc.RPC('getblockcount').execute()
    assert post.call_args[1]['timeout'] == (5, 30)


def test_lagging_node_does_not_invalidate_verified_data(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 9)
    with pytest.raises(RuntimeError, match='behind'):
        daemon.update_blockchain()
    assert query_cache.get_generation() == 'initial'
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 10)
    daemon.update_blockchain()
    assert query_cache.get_generation() == 'initial'


def test_null_tip_hash_is_not_misclassified_as_a_reorg(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 10)
    monkeypatch.setattr(daemon, 'get_block_hash', lambda height: None)
    with pytest.raises(RuntimeError, match='RPC'):
        daemon.update_blockchain()
    assert query_cache.get_generation() == 'initial'


def test_completed_writes_resume_verification_without_replaying_balances(daemon, monkeypatch):
    error_type = getattr(rpc, 'RPCError', RuntimeError)
    counts = Mock(side_effect=[11, error_type('RPC unavailable'), 11])
    monkeypatch.setattr(daemon, 'get_block_count', counts)
    monkeypatch.setattr(daemon, 'get_block', lambda height: block(height, [
        transaction('coinbase', [{'coinbase': '00'}], ['address-one']),
    ]))
    with pytest.raises(error_type, match='RPC unavailable'):
        daemon.update_blockchain()

    state = CacheState.objects.get(id='blockchain')
    assert getattr(state, 'writes_complete', False) is True
    assert state.ready is False
    assert Address.objects.get(hash='address-one').balance == 9

    monkeypatch.setattr(daemon, 'insert_block', Mock(side_effect=AssertionError('must not replay')))
    daemon.update_blockchain()
    assert query_cache.get_generation() is not None
    assert Address.objects.get(hash='address-one').balance == 9


def test_pending_verification_cannot_publish_a_changed_tip(daemon, monkeypatch):
    error_type = getattr(rpc, 'RPCError', RuntimeError)
    monkeypatch.setattr(daemon, 'get_block_count', Mock(side_effect=[11, error_type('RPC unavailable'), 11]))
    monkeypatch.setattr(daemon, 'get_block', lambda height: block(height, [
        transaction('coinbase', [{'coinbase': '00'}], ['address-one']),
    ]))
    with pytest.raises(error_type):
        daemon.update_blockchain()
    monkeypatch.setattr(daemon, 'get_block_hash', lambda height: 'other-fork')
    with pytest.raises(RuntimeError, match='canonical'):
        daemon.update_blockchain()
    assert query_cache.get_generation() is None
    with pytest.raises(RuntimeError, match='unfinished'):
        daemon.update_blockchain()


def test_daemon_retries_only_classified_rpc_errors(daemon, monkeypatch):
    error_type = getattr(rpc, 'RPCError', RuntimeError)
    update = Mock(side_effect=[error_type('RPC unavailable'), RuntimeError('data failure')])
    monkeypatch.setattr(daemon, 'try_to_connect', lambda: None)
    monkeypatch.setattr(daemon, 'update_blockchain', update)
    monkeypatch.setattr(daemon, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='data failure'):
        daemon.run()
    assert update.call_count == 2


def test_obsolete_writer_cannot_mark_writes_complete():
    token = query_cache.begin_update()
    query_cache.invalidate()
    with pytest.raises(RuntimeError, match='generation changed'):
        query_cache.mark_writes_complete(token, 11, 'block-11')
    assert query_cache.get_pending_verification() is None


@pytest.mark.parametrize('result', [None, True, -1, '10', 10.5])
def test_invalid_rpc_height_is_not_accepted(monkeypatch, result):
    response = Mock()
    response.json.return_value = {'result': result, 'error': None}
    monkeypatch.setattr(rpc.requests, 'post', Mock(return_value=response))
    with pytest.raises(RuntimeError, match='RPC'):
        rpc.get_block_count()


def test_rpc_does_not_include_credentials_in_transport_errors(monkeypatch):
    post = Mock(side_effect=requests.ConnectionError('http://secret-user:secret-pass@host'))
    monkeypatch.setattr(rpc.requests, 'post', post)
    with pytest.raises(RuntimeError) as failure:
        rpc.get_block_hash(1)
    assert 'secret-' not in str(failure.value)
    assert failure.value.__suppress_context__ is True


@pytest.mark.parametrize('method,argument,result', [
    ('get_block_hash', 10, 'block-10'),
    ('get_block_count', None, 10),
    ('get_hashrate', None, 123),
    ('get_raw_transaction', 'tx-1', {'txid': 'tx-1', 'vout': []}),
])
def test_valid_rpc_results_keep_their_contract(monkeypatch, method, argument, result):
    response = Mock()
    response.json.return_value = {'result': result, 'error': None}
    monkeypatch.setattr(rpc.requests, 'post', Mock(return_value=response))
    function = getattr(rpc, method)
    assert (function() if argument is None else function(argument)) == result


@pytest.mark.parametrize('result', [{'hash': 'block-10'}, []])
def test_get_block_validates_rpc_result(monkeypatch, result):
    response = Mock()
    response.json.side_effect = [
        {'result': 'block-10', 'error': None}, {'result': result, 'error': None},
    ]
    monkeypatch.setattr(rpc.requests, 'post', Mock(return_value=response))
    if isinstance(result, dict):
        assert rpc.get_block(10) == result
    else:
        with pytest.raises(RuntimeError, match='RPC'):
            rpc.get_block(10)


def test_invalid_rpc_hash_is_rejected(monkeypatch):
    response = Mock()
    response.json.return_value = {'result': 42, 'error': None}
    monkeypatch.setattr(rpc.requests, 'post', Mock(return_value=response))
    with pytest.raises(RuntimeError, match='RPC'):
        rpc.get_block_hash(10)


def test_rpc_error_during_partial_writes_stops_without_retrying(daemon, monkeypatch):
    query_cache.begin_update()
    update = Mock(side_effect=[rpc.RPCError('RPC unavailable'), RuntimeError('unexpected retry')])
    monkeypatch.setattr(daemon, 'try_to_connect', lambda: None)
    monkeypatch.setattr(daemon, 'update_blockchain', update)
    monkeypatch.setattr(daemon, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='unfinished'):
        daemon.run()
    assert update.call_count == 1


def test_btcv_alert_metadata_is_preserved_without_reclassifying_alert_outputs(daemon):
    incoming = block(11, [transaction('regular', [{'coinbase': '00'}], ['recipient'])])
    alert = transaction('alert', [{'txid': 'regular', 'vout': 0}], ['alert-recipient'])
    alert.update(type='alert', status='pending')
    incoming['atx'] = [alert]
    new, deleted = {}, {}
    db.insert_block(incoming, new, deleted)
    daemon.update_addresses(new, deleted)
    stored = Block.objects.get(hash='block-11')
    assert stored.atx[0].txid == 'alert'
    assert stored.atx[0].type == 'alert'
    assert stored.atx[0].status == 'pending'
    assert UTXO.objects.get(id='regular0').value == 2
    assert Address.objects.get(hash='recipient').balance == 2
    assert Address.objects(hash='alert-recipient').first() is None
