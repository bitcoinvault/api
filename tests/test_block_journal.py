"""Protocol boundaries without network or MongoDB; use the daemon's dependencies."""
import copy
import hashlib
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from bson import BSON
    from db import block_recovery as recovery
except ModuleNotFoundError as error:
    if error.name not in ('bson', 'mongoengine', 'pymongo'):
        raise
    recovery = None


REGTEST = '01cead69f2b51e214e1c2cd50e3744428cae526db87b2ff8f32489ff801d0f1d'
MAINNET = '0000000028ce26975b32feda3d75ac3fe10372f75062366cfba4e934dcc6a48b'


def transfer(txid, inputs, outputs):
    return {'txid': txid, 'hash': txid, 'hex': txid, 'type': 'TX_NONVAULT', 'vin': inputs, 'vout': [
        {'value': value, 'n': n, 'scriptPubKey': {'addresses': [addr], 'type': 'pubkeyhash',
         'hex': '76a914' + '11'*20 + '88ac'} if addr else {'type': 'nulldata', 'hex': '6a'}}
        for n, (addr, value) in enumerate(outputs)]}


def block(tx):
    return {'hash': 'b' * 64, 'height': 1, 'previousblockhash': REGTEST,
            'time': 1300000010, 'difficulty': 2.0, 'tx': tx, 'atx': [], 'alertmerkleroot': '0' * 64,
            'nTx': len(tx), 'merkleroot': recovery.transaction_merkle_root(tx)}


@unittest.skipUnless(recovery, 'requires daemon runtime dependencies')
class PlanningTests(unittest.TestCase):
    tip = {'_id': REGTEST, 'height': 0}

    def test_same_block_spend_and_smallest_unit(self):
        transactions = [transfer('c'*64, [{'coinbase': '00'}], [('a', 0.1), ('', 0)]),
                        transfer('d'*64, [{'txid': 'c'*64, 'vout': 0}],
                                 [('a', 0.09999999), ('b', 0.00000001)])]
        transactions[1]['type'] = 'TX_INSTANT'
        trusted = copy.deepcopy(transactions[1])
        with patch.object(recovery, 'load', return_value={}), \
                patch.object(recovery, 'decode_raw_transaction', return_value=trusted) as decode:
            plan = recovery.plan(block(transactions), self.tip, REGTEST)
        decode.assert_called_once_with('d'*64)
        self.assertEqual([row['key'] for row in plan['utxos']], ['d'*64+'0', 'd'*64+'1'])
        balances = {a['key']: Decimal(str(a['after']['balance'])) for a in plan['addresses']}
        self.assertEqual(balances, {'a': Decimal('0.09999999'), 'b': Decimal('0.00000001')})

    def test_regular_commitment_uses_ordered_txids_not_witness_hashes(self):
        ids = [format(i, '064x') for i in (1, 2, 3)]
        txs = [transfer(key, [{'txid': ids[i-1], 'vout': 0}] if i else [{'coinbase': '00'}],
                        [('a', 1)]) for i, key in enumerate(ids)]
        for tx in txs:
            tx['hash'] = 'f'*64
        raw = block(txs)
        expected = 'e9ffb584c62449f157c8be88257bd1eebb2d8ef824f5c86b43c4f8fd9e800d6a'
        self.assertEqual(raw['merkleroot'], expected)
        self.assertEqual(recovery.alert_merkle_root(txs), expected)
        with patch.object(recovery, 'load', return_value={}):
            recovery.plan(copy.deepcopy(raw), self.tip, REGTEST)
        raw['tx'].reverse()  # keep the original count and root
        self.assert_rejected_before_db(raw, 'regular transaction merkle commitment')

    def assert_rejected_before_db(self, raw, reason, genesis=REGTEST):
        with patch.object(recovery, 'collection') as db, patch.object(recovery, 'load') as reads:
            with self.assertRaisesRegex(ValueError, reason):
                recovery.plan(raw, {'_id': genesis, 'height': 0} if raw['height'] else None, genesis)
            db.assert_not_called()
            reads.assert_not_called()

    def test_regular_commitment_required_even_at_genesis_and_before_activation(self):
        tx = transfer('d'*64, [{'coinbase': '00'}], [('a', 1)])
        for height, genesis in ((0, MAINNET), (1, MAINNET), (1, REGTEST)):
            for key, value in (('nTx', None), ('nTx', True), ('nTx', 2),
                               ('merkleroot', None), ('merkleroot', 'e'*64)):
                raw = block([copy.deepcopy(tx)])
                raw.update(height=height, hash='b'*64 if height else genesis,
                           previousblockhash=genesis if height else '0'*64)
                if value is None:
                    del raw[key]
                else:
                    raw[key] = value
                with self.subTest(height=height, genesis=genesis, key=key, value=value):
                    reason = 'regular transaction count' if key == 'nTx' else 'regular transaction merkle commitment'
                    self.assert_rejected_before_db(raw, reason, genesis)

    def test_ingest_rejects_bad_regular_count_before_reading_checkpoint(self):
        raw = block([transfer('d'*64, [{'coinbase': '00'}], [('a', 1)])])
        raw['nTx'] = True  # compares equal to 1 unless the integer type is checked
        with patch.object(recovery, 'checkpoint', return_value=self.tip) as checkpoint, \
                patch.object(recovery, 'collection') as db:
            with self.assertRaisesRegex(ValueError, 'regular transaction count'):
                recovery.ingest_block(raw, lambda h: REGTEST if h == 0 else raw['hash'])
            checkpoint.assert_not_called()
            db.assert_not_called()

    def test_vault_typed_regular_projection_matches_separate_decode_before_db(self):
        tx = transfer('d'*64, [{'txid': 'a'*64, 'vout': 0}], [('a', 1)])
        changes = [
            (('txid',), 'e'*64), (('hash',), 'f'*64),
            (('vin', 0, 'txid'), 'e'*64), (('vin', 0, 'vout'), 1),
            (('vout', 0, 'n'), 1), (('vout', 0, 'value'), 2),
            (('vout', 0, 'scriptPubKey', 'hex'), '51'), (('vout', 0, 'scriptPubKey', 'type'), 'scripthash'),
            (('vout', 0, 'scriptPubKey', 'addresses'), ['b']),
        ]
        for typ in ('TX_INSTANT', 'TX_ALERT', 'TX_RECOVERY'):
            tx['type'] = typ
            for path, value in changes:
                original = copy.deepcopy(tx)
                target = original
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = value
                raw = block([copy.deepcopy(tx)])
                raw['previousblockhash'] = MAINNET  # height 1, before vault activation
                with self.subTest(typ=typ, path=path), \
                        patch.object(recovery, 'decode_raw_transaction', return_value=original) as decode:
                    reason = 'identity mismatch' if path[0] in ('txid', 'hash') else 'accounting fields differ'
                    self.assert_rejected_before_db(raw, reason, MAINNET)
                    decode.assert_called_once_with(tx['hex'])

    def test_missing_input_and_duplicate_output_refused(self):
        cases = [[transfer('e'*64, [{'txid': 'c'*64, 'vout': 0}], [('a', 1)])],
                 [transfer('d'*64, [{'coinbase': '00'}], [('a', 1)])] * 2]
        for txs in cases:
            with self.subTest(txs=txs), patch.object(recovery, 'load', return_value={}):
                with self.assertRaises(ValueError):
                    recovery.plan(block(copy.deepcopy(txs)), self.tip, REGTEST)

    def test_wrong_parent_refused_before_data_reads(self):
        with patch.object(recovery, 'load') as read:
            with self.assertRaisesRegex(ValueError, 'checkpoint'):
                recovery.plan(block([transfer('d'*64, [{'coinbase': '00'}], [('a', 1)])]),
                              dict(self.tip, _id='1'*64), REGTEST)
            read.assert_not_called()

    def test_invalid_amount_refused(self):
        for x in (-1, 'NaN', 'Infinity', '0.000000001'):
            with self.subTest(x=x), self.assertRaises(ValueError):
                recovery.amount(x)

    def test_oversized_intent_never_written(self):
        with patch.object(recovery, 'plan', return_value={'large': 'a'*150}), \
                patch.object(recovery, 'checkpoint'), patch.object(recovery, 'MAX_BSON', 128), \
                patch.object(recovery, 'collection') as db:
            with self.assertRaisesRegex(ValueError, 'BSON'):
                recovery.ingest_block({}, lambda _: REGTEST)
            db.assert_not_called()

    def test_corrupt_intent_never_replayed(self):
        with patch.object(recovery, 'collection') as db:
            with self.assertRaisesRegex(ValueError, 'journal'):
                recovery._apply({'body': {'schema': 1}, 'checksum': 'wrong'}, lambda _: None)
            db.assert_not_called()

    def test_reorganized_pending_block_not_applied(self):
        body = {'schema': 1, 'block': {'_id': 'b'*64, 'height': 23}}
        stored = {'body': body, 'checksum': hashlib.sha256(BSON.encode(body)).hexdigest()}
        with patch.object(recovery, 'collection', return_value=Mock(find_one=Mock(return_value=stored))), \
                patch.object(recovery, 'apply_edits') as apply:
            with self.assertRaisesRegex(ValueError, 'canonical'):
                recovery.apply_pending(lambda _: 'a'*64)
            apply.assert_not_called()

    def test_unsupported_shapes_do_not_create_an_intent(self):
        tx = transfer('d'*64, [{'coinbase': '00'}], [('a', 1), ('', 0)])
        transformations = [
            lambda b: b.pop('atx'),
            lambda b: b.update(atx=[tx]),
            lambda b: b['tx'][0].pop('type'),
            lambda b: b['tx'][0].update(type='TX_ALERT'),
            lambda b: b['tx'][0].update(type='TX_RECOVERY'),
            lambda b: b['tx'][0].update(type='TX_INVALID'),
            lambda b: b['tx'][0]['vout'][0]['scriptPubKey'].update(addresses=[12]),
            lambda b: b['tx'][0]['vout'][1]['scriptPubKey'].update(type='nonstandard'),
            lambda b: b['tx'][0]['vin'][0].update(txid='c'*64, vout=0),
            lambda b: b['tx'][0]['vout'][1].update(n=0),
            lambda b: b['tx'][0].update(txid='not-a-hash')]
        for change in transformations:
            raw = block([copy.deepcopy(tx)])
            change(raw)
            with self.subTest(raw=raw), patch.object(recovery, 'checkpoint', return_value=self.tip), \
                    patch.object(recovery, 'collection') as db, self.assertRaises(ValueError):
                try:
                    recovery.ingest_block(raw, lambda h: REGTEST if h == 0 else raw['hash'])
                finally:
                    db.assert_not_called()

    def test_duplicate_block_id_never_written(self):
        body = {'block': {'_id': 'b'*64, 'height': 1}}
        with patch.object(recovery, 'plan', return_value=body), patch.object(recovery, 'checkpoint'), \
                patch.object(recovery, 'collection') as coll, self.assertRaisesRegex(ValueError, 'already exists'):
            try:
                recovery.ingest_block({}, lambda h: REGTEST if h == 0 else 'b'*64)
            finally:
                coll.return_value.insert_one.assert_not_called()

    def test_reorg_before_or_after_checkpoint_retains_intent(self):
        h = 'b'*64
        body = {'schema': 1, 'previous': self.tip, 'block': {'height': 1, '_id': h}, 'utxos': [], 'addresses': []}
        intent = {'body': body, 'checksum': hashlib.sha256(BSON.encode(body)).hexdigest()}
        for checks, inserts in (([h, 'x'*64], 0), ([h, h, 'x'*64], 1)):
            db = Mock(find_one=Mock(return_value=None))
            with self.subTest(checks=checks), patch.object(recovery, 'collection', return_value=db), \
                    patch.object(recovery, 'checkpoint', return_value=self.tip), patch.object(recovery, 'apply_edits'):
                with self.assertRaisesRegex(ValueError, 'canonical'):
                    recovery._apply(intent, Mock(side_effect=checks))
                self.assertEqual(db.insert_one.call_count, inserts)
                db.delete_one.assert_not_called()
