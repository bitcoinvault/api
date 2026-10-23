import importlib
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from db import db, query_cache
from db.models import Address, Block, Tx, UTXO, Vin
from mongoengine.errors import NotUniqueError


@pytest.fixture
def daemon(monkeypatch, chain_data):
    monkeypatch.setitem(sys.modules, 'mongobackup', SimpleNamespace(backup=Mock(), restore=Mock()))
    module = importlib.import_module('daemon')
    module.update_analyzer()
    monkeypatch.setattr(module, 'get_block_hash', lambda height: 'block-' + str(height), raising=False)
    for name in ['piechart_data', 'mining_difficulty', 'transaction_volume']:
        monkeypatch.setattr(module.analyzer, name, lambda *args: [])
    return module


def test_successful_sync_publishes_only_after_address_update(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 11)
    monkeypatch.setattr(daemon, 'get_block', lambda height: {
        'hash': 'block-' + str(height), 'height': height,
        'previousblockhash': 'block-' + str(height - 1),
    })

    def insert(block, new, deleted):
        assert query_cache.get_generation() is None
        Block(hash=block['hash'], height=11, time=1, difficulty=1,
              insert_time=1, update_time=1).save()

    def update(new, deleted):
        assert query_cache.get_generation() is None
        Address.objects.update(set__balance=9)

    monkeypatch.setattr(daemon, 'insert_block', insert)
    monkeypatch.setattr(daemon, 'update_addresses', update)
    daemon.update_blockchain()
    assert query_cache.get_generation() not in (None, 'initial')
    assert Address.objects.first().balance == 9


def test_failed_address_update_leaves_cache_disabled(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 11)
    monkeypatch.setattr(daemon, 'get_block', lambda height: {
        'hash': 'block-' + str(height), 'height': height,
        'previousblockhash': 'block-' + str(height - 1),
    })
    monkeypatch.setattr(daemon, 'insert_block', lambda *args: None)
    monkeypatch.setattr(daemon, 'update_addresses', Mock(side_effect=RuntimeError('write failed')))
    with pytest.raises(RuntimeError, match='write failed'):
        daemon.update_blockchain()
    assert query_cache.get_generation() is None
    with pytest.raises(RuntimeError, match='unfinished'):
        daemon.update_blockchain()


def test_same_height_reorg_disables_cache_before_mutation(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 10)
    monkeypatch.setattr(daemon, 'get_block_hash', lambda height: 'different-fork')
    insert = Mock()
    monkeypatch.setattr(daemon, 'insert_block', insert)
    with pytest.raises(RuntimeError, match='canonical'):
        daemon.update_blockchain()
    insert.assert_not_called()
    assert query_cache.get_generation() is None


def test_reorg_during_sync_does_not_publish(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 11)
    monkeypatch.setattr(daemon, 'get_block_hash', lambda height: 'block-10' if height == 10 else 'other-fork')
    monkeypatch.setattr(daemon, 'get_block', lambda height: {
        'hash': 'block-11', 'height': height, 'previousblockhash': 'block-10',
    })

    def insert(block, new, deleted):
        Block(hash='block-11', height=11, time=1, difficulty=1,
              insert_time=1, update_time=1).save()

    monkeypatch.setattr(daemon, 'insert_block', insert)
    monkeypatch.setattr(daemon, 'update_addresses', lambda *args: None)
    with pytest.raises(RuntimeError, match='canonical'):
        daemon.update_blockchain()
    assert query_cache.get_generation() is None


def test_missing_utxo_is_not_silently_ignored(chain_data, monkeypatch):
    transaction = Tx(txid='test-tx', vin=[Vin(txid='missing', vout=0)])
    if 'hash' in Tx._fields:
        transaction.hash = 'test-tx'
    block = Block(hash='bad-block', height=11, time=1, difficulty=1,
                  insert_time=1, update_time=1, tx=[transaction])
    monkeypatch.setattr(db, 'create_block', lambda incoming: block)
    with pytest.raises(UTXO.DoesNotExist):
        db.insert_block({'hash': 'bad-block'}, {}, {})
    assert Block.objects(hash='bad-block').first() is None


def test_noop_tick_does_not_enable_uninitialized_cache(daemon, monkeypatch, clean_database):
    clean_database.cache_state.delete_many({})
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 10)
    daemon.update_blockchain()
    assert query_cache.get_generation() is None


def test_node_behind_stored_tip_requires_recovery(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 9)
    with pytest.raises(RuntimeError, match='canonical'):
        daemon.update_blockchain()
    assert query_cache.get_generation() is None


def test_address_write_conflict_is_not_silently_ignored(monkeypatch):
    address = Address(hash='new', balance=1, insert_time=1, update_time=1)
    monkeypatch.setattr(Address, 'save', Mock(side_effect=NotUniqueError('write conflict')))
    with pytest.raises(NotUniqueError):
        db.insert_address(address)


def test_new_block_must_extend_verified_anchor(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 11)
    monkeypatch.setattr(daemon, 'get_block_hash', lambda height: 'block-' + str(height))
    monkeypatch.setattr(daemon, 'get_block', lambda height: {
        'hash': 'block-11', 'height': height, 'previousblockhash': 'different-block-10',
    })
    insert = Mock()
    monkeypatch.setattr(daemon, 'insert_block', insert)
    with pytest.raises(RuntimeError, match='canonical'):
        daemon.update_blockchain()
    insert.assert_not_called()
    assert query_cache.get_generation() is None


def test_block_with_unexpected_height_is_rejected(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 11)
    monkeypatch.setattr(daemon, 'get_block', lambda height: {
        'hash': 'block-12', 'height': 12, 'previousblockhash': 'block-10',
    })
    insert = Mock()
    monkeypatch.setattr(daemon, 'insert_block', insert)
    with pytest.raises(RuntimeError, match='canonical'):
        daemon.update_blockchain()
    insert.assert_not_called()
    assert query_cache.get_generation() is None


def test_real_coinbase_and_spend_update_balances_before_cache_publication(daemon, monkeypatch):
    height = [11]
    monkeypatch.setattr(daemon, 'get_block_count', lambda: height[0])

    def block(number):
        transaction = {
            'txid': 'tx-' + str(number),
            'vin': [{'coinbase': '00'}] if number == 11 else [{'txid': 'tx-11', 'vout': 0}],
            'vout': [{'n': 0, 'value': 2, 'scriptPubKey': {
                'addresses': ['address-one' if number == 11 else 'address-two'],
            }}],
        }
        if 'hash' in Tx._fields:
            transaction['hash'] = transaction['txid']
        return {'hash': 'block-' + str(number), 'height': number,
                'previousblockhash': 'block-' + str(number - 1),
                'time': number, 'difficulty': 1, 'tx': [transaction]}

    monkeypatch.setattr(daemon, 'get_block', block)
    daemon.update_blockchain()
    first_generation = query_cache.get_generation()
    assert first_generation not in (None, 'initial')
    assert Address.objects.get(hash='address-one').balance == 9
    assert UTXO.objects.get(id='tx-110').value == 2

    height[0] = 12
    daemon.update_blockchain()
    assert query_cache.get_generation() not in (None, first_generation)
    assert Address.objects.get(hash='address-one').balance == 7
    assert Address.objects.get(hash='address-two').balance == 2
    assert UTXO.objects(id='tx-110').first() is None
    assert UTXO.objects.get(id='tx-120').value == 2


def test_rpc_failure_before_first_write_keeps_previous_ready_state(daemon, monkeypatch):
    monkeypatch.setattr(daemon, 'get_block_count', lambda: 11)
    monkeypatch.setattr(daemon, 'get_block', Mock(side_effect=ConnectionError('RPC unavailable')))
    with pytest.raises(ConnectionError, match='RPC unavailable'):
        daemon.update_blockchain()
    assert query_cache.get_generation() == 'initial'
    assert Block.objects.count() == 1
