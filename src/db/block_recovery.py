"""Single-writer, forward-only block accounting for standalone MongoDB."""
import hashlib
from decimal import Decimal
from itertools import chain
import re
from time import time

from bson import BSON
from mongoengine.connection import get_db
from pymongo import DeleteOne, InsertOne, ReplaceOne
from pymongo.write_concern import WriteConcern

from db.db_utils import create_block
from db.models import Address, Block, UTXO


PENDING = 'daemon_pending_block'
KEY = 'active'
MAX_BSON = 16 * 1024 * 1024
SAT = Decimal('0.00000001')
WC = WriteConcern(w=1, j=True)


def collection(name):
    return get_db().get_collection(name, write_concern=WC)


def checkpoint():
    rows = list(collection(Block._get_collection_name()).find(
        {}, {'height': 1}).sort('height', -1).limit(2))
    if not rows:
        return None
    if len(rows) == 2 and rows[0]['height'] == rows[1]['height']:
        raise ValueError('ambiguous committed block height')
    return rows[0]


def amount(value):
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or result != result.quantize(SAT):
        raise ValueError('invalid indexed amount')
    return result


def load(name, keys):
    if not keys:
        return {}
    return {doc['_id']: doc for doc in collection(name).find({'_id': {'$in': list(keys)}})}


def edits(original, desired):
    return [{'key': key, 'before': original.get(key), 'after': desired.get(key)}
            for key in sorted(original.keys() | desired.keys())
            if original.get(key) != desired.get(key)]


def validate_accounting_shape(raw):
    def hash_value(s):
        return isinstance(s, str) and re.fullmatch('[0-9a-f]{64}', s)

    def output_index(n):
        return type(n) is int and 0 <= n < 2**32

    if not hash_value(raw.get('hash')) or type(raw.get('height')) is not int or raw['height'] < 0:
        raise ValueError('invalid block identity')
    if not isinstance(raw.get('atx'), list) or (raw['height'] == 0 and raw['atx']):
        raise ValueError('invalid or missing alert transaction list')
    if not isinstance(raw.get('tx'), list) or not raw['tx']:
        raise ValueError('missing block transactions')
    txids = set()
    regular_types = ('TX_NONVAULT', 'TX_INSTANT', 'TX_ALERT', 'TX_RECOVERY')
    for tx, allowed in chain(((tx, regular_types) for tx in raw['tx']),
                             ((tx, ('TX_ALERT',)) for tx in raw['atx'])):
        if not isinstance(tx, dict) or tx.get('type') not in allowed:
            raise ValueError('unsupported transaction type for block list')
        if not hash_value(tx.get('txid')) or not hash_value(tx.get('hash')) or tx['txid'] in txids:
            raise ValueError('invalid or repeated transaction identity')
        txids.add(tx['txid'])
        if not isinstance(tx.get('vin'), list) or not tx['vin'] or not isinstance(tx.get('vout'), list):
            raise ValueError('invalid transaction inputs or outputs')
        for vin in tx['vin']:
            if 'coinbase' in vin:
                if (tx['type'] != 'TX_NONVAULT' or len(tx['vin']) != 1
                        or not isinstance(vin['coinbase'], str) or not vin['coinbase']
                        or 'txid' in vin or 'vout' in vin):
                    raise ValueError('ambiguous coinbase input')
            elif not hash_value(vin.get('txid')) or not output_index(vin.get('vout')):
                raise ValueError('invalid spent output reference')
        for i, vout in enumerate(tx['vout']):
            if not output_index(vout.get('n')) or vout['n'] != i:
                raise ValueError('invalid output position')
            amount(vout['value'])
            script = vout['scriptPubKey']
            addresses = script.get('addresses', [])
            if not addresses:
                if (addresses != [] or script.get('type') != 'nulldata'
                        or not re.fullmatch('6a(?:[0-9a-f]{2})*', script.get('hex', ''))):
                    raise ValueError('unsupported addressless output')
            elif (not isinstance(addresses, list) or len(addresses) != 1
                  or not isinstance(addresses[0], str) or not addresses[0].strip()
                  or script.get('type') == 'nulldata'):
                raise ValueError('unsupported output address set')


def check_reservation(spent, tx, height, groups):
    """Account confirmed totals, not wallet spendability; Core validates consensus timing."""
    settlement = tx.type in ('TX_ALERT', 'TX_RECOVERY')
    if 'pending_alert' not in spent:
        if settlement:
            raise ValueError('vault settlement without an indexed reservation')
        return
    mark = spent['pending_alert']
    if (not isinstance(mark, dict) or not isinstance(mark.get('txid'), str)
            or not re.fullmatch('[0-9a-f]{64}', mark['txid'])
            or type(mark.get('height')) is not int or not 0 < mark['height'] < height
            or type(mark.get('input_count')) is not int or mark['input_count'] <= 0):
        raise ValueError('invalid indexed vault reservation')
    if not settlement or (tx.type == 'TX_ALERT' and tx.txid != mark['txid']):
        raise ValueError('transaction cannot spend the reserved input')
    group = groups.setdefault(mark['txid'], [mark, 0])
    if mark != group[0]:
        raise ValueError('inconsistent vault reservation')
    group[1] += 1


def plan(raw, previous):
    validate_accounting_shape(raw)
    block = create_block(raw)
    if not previous:
        if block.height != 0 or block.previousblockhash not in ('', '0' * 64):
            raise ValueError('expected genesis')
    elif block.height != previous['height'] + 1 or block.previousblockhash != previous['_id']:
        raise ValueError('block does not extend the committed checkpoint')
    stamp = time()
    block.insert_time = block.update_time = stamp
    block.validate()
    if block.height == 0:
        # Core records genesis but never adds its coinbase to the UTXO set.
        return {'schema': 1, 'previous': previous, 'block': block.to_mongo().to_dict(),
                'utxos': [], 'addresses': []}
    keys = {v.txid + str(v.vout) for tx in chain(block.tx, block.atx) for v in tx.vin if 'coinbase' not in v}
    keys.update(tx.txid + str(v.n) for tx in block.tx for v in tx.vout)
    original = load(UTXO._get_collection_name(), keys)
    desired, deltas, created = original.copy(), {}, set()
    for tx in block.tx:
        groups = {}
        for vin in tx.vin:
            if 'coinbase' in vin:
                continue
            spent = desired.pop(vin.txid + str(vin.vout), None)
            if spent is None:
                raise ValueError('unknown or already spent input; block not committed')
            check_reservation(spent, tx, block.height, groups)
            addr = spent['address']
            deltas[addr] = deltas.get(addr, Decimal(0)) - amount(spent['value'])
        if any(count != mark['input_count'] for mark, count in groups.values()):
            raise ValueError('incomplete vault settlement')
        for vout in tx.vout:
            addresses = vout.scriptPubKey.addresses
            if not addresses:
                continue
            if len(addresses) != 1:
                raise ValueError('unsupported output address set')
            addr, key = addresses[0], tx.txid + str(vout.n)
            if key in created or key in original:
                raise ValueError('duplicate output reference')
            created.add(key)
            desired[key] = dict(_id=key, address=addr, value=vout.value, insert_time=stamp, update_time=stamp)
            deltas[addr] = deltas.get(addr, Decimal(0)) + amount(vout.value)

    # Core processes vatx after vtx: reserve original inputs without crediting prospective outputs.
    for alert in block.atx:
        mark = dict(txid=alert.txid, height=block.height, input_count=len(alert.vin))
        for vin in alert.vin:
            key = vin.txid + str(vin.vout)
            coin = desired.get(key)
            if coin is None or 'pending_alert' in coin:
                raise ValueError('missing or already reserved alert input')
            desired[key] = dict(coin, pending_alert=mark, update_time=stamp)

    old_addresses = load(Address._get_collection_name(), deltas)
    new_addresses = {}
    for addr, delta in deltas.items():
        doc = old_addresses.get(addr, dict(_id=addr, balance=0.0, txs=[], insert_time=stamp))
        balance = amount(doc['balance']) + delta
        stored = float(balance)
        if amount(stored) != balance:
            raise ValueError('balance cannot be represented by the existing schema')
        new_addresses[addr] = dict(doc, balance=stored, update_time=stamp) if delta or addr not in old_addresses else doc
    uses_vault = bool(block.atx) or any(tx.type in ('TX_ALERT', 'TX_RECOVERY') for tx in block.tx)
    return {'schema': 2 if uses_vault else 1, 'previous': previous, 'block': block.to_mongo().to_dict(),
            'utxos': edits(original, desired), 'addresses': edits(old_addresses, new_addresses)}


def apply_edits(name, changes, already_committed):
    actual = load(name, (change['key'] for change in changes))
    ops = []
    for change in changes:
        key, before, after = change['key'], change['before'], change['after']
        current = actual.get(key)
        if current == after:
            continue
        if already_committed or current != before:
            raise ValueError('indexed data disagrees with the pending block')
        if before is None:
            ops.append(InsertOne(after))
        else:
            predicate = {'_id': key, '$expr': {'$eq': ['$$ROOT', {'$literal': before}]}}
            ops.append(DeleteOne(predicate) if after is None else ReplaceOne(predicate, after))
    if ops:
        result = collection(name).bulk_write(ops, ordered=True)
        if result.inserted_count + result.deleted_count + result.matched_count != len(ops):
            raise ValueError('index changed while applying pending block')


def valid_body(intent):
    body = intent['body']
    if body['schema'] not in (1, 2) or hashlib.sha256(BSON.encode(body)).hexdigest() != intent['checksum']:
        raise ValueError('invalid pending block journal')
    return body


def require_canonical(block, check_hash):
    if check_hash(block['height']) != block['_id']:
        raise ValueError('pending block is no longer canonical')


def _apply(intent, check_hash):
    body = valid_body(intent)
    block = body['block']
    require_canonical(block, check_hash)
    tip = checkpoint()
    published = tip == {'_id': block['_id'], 'height': block['height']}
    if not published and tip != body['previous']:
        raise ValueError('pending block conflicts with committed checkpoint')
    blocks = collection(Block._get_collection_name())
    existing = blocks.find_one({'_id': block['_id']})
    if existing != (block if published else None):
        raise ValueError('block identity conflicts with the journal')
    apply_edits(UTXO._get_collection_name(), body['utxos'], published)
    apply_edits(Address._get_collection_name(), body['addresses'], published)
    if not published:
        require_canonical(block, check_hash)
        blocks.insert_one(block)
    require_canonical(block, check_hash)
    if collection(PENDING).delete_one({'_id': KEY, 'checksum': intent['checksum']}).deleted_count != 1:
        raise ValueError('pending block ownership changed')


def apply_pending(check_hash):
    intent = collection(PENDING).find_one({'_id': KEY})
    if not intent:
        return False
    _apply(intent, check_hash)
    return True


def ingest_block(raw, check_hash):
    body = plan(raw, checkpoint())
    intent = {'_id': KEY, 'body': body, 'checksum': hashlib.sha256(BSON.encode(body)).hexdigest()}
    if len(BSON.encode(intent)) >= MAX_BSON:
        raise ValueError('block journal exceeds BSON limit; no index changes applied')
    block = body['block']
    if collection(Block._get_collection_name()).find_one({'_id': block['_id']}, {'_id': 1}):
        raise ValueError('block identity already exists')
    require_canonical(block, check_hash)
    collection(PENDING).insert_one(intent)
    _apply(intent, check_hash)
