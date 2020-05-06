from datetime import datetime
from models import Address, Block, UTXO
from mongoengine import Document
from rpc import get_block, get_block_count
import json

db_host = 'mongodb://mongo:27017/blockchain'
db_name = 'blockchain'

def drop_db():
    Block.drop_collection()
    Address.drop_collection()
    UTXO.drop_collection()
    
def _set_insert_or_update_time(db_object, ins=True, upd=True):
    time = datetime.now().timestamp()
    
    if ins:
        db_object.insert_time = time
    if upd:
        db_object.update_time = time
    
def get_highest_block_number_in_db():
    if Block.objects().count() == 0:
        return -1
    return Block.objects().order_by('-height').limit(1)[0].height

def _create_utxo(tx_key, addr, amount):
    json_utxo = json.dumps({'id':tx_key, 'address':addr, 'value':amount})
    return UTXO.from_json(json_utxo)
    
def _parse_inputs(txid, vin, utxo, new_utxo):
    for inp in vin:
        if 'coinbase' in inp:
            continue
        in_txid = inp.txid
        in_vout = inp.vout
        tx_key = in_txid + str(in_vout)
        try: 
            db_utxo = utxo.get(id=tx_key)
            if db_utxo.id in new_utxo:
                del new_utxo[db_utxo.id]
            db_utxo.delete()
            print('Deleted utxo: ' + db_utxo.to_json())
        except:
            exception_string = "Invalid input. txid: " + str(txid) + ", vin_txid: " + str(
                in_txid) + ", vout: " + str(in_vout)
            raise BaseException(exception_string)
        
def _parse_outputs(txid, vout, utxo, new_utxo):
    for out in vout:
        out_addresses = out.scriptPubKey.addresses
        if out_addresses == []:
            continue
        out_addr_len = len(out_addresses)
        if out_addr_len != 1:
            exception_string = "Bad address length: " + out_addr_len + " txid: " + str(txid)
            raise BaseException(exception_string)
        out_n = out.n
        out_addr = out_addresses[0]
        tx_key = txid + str(out_n)
        db_utxo = _create_utxo(tx_key, out_addr, out.value)
        if not utxo(id=db_utxo.id):
            _set_insert_or_update_time(db_utxo)
            db_utxo.save(force_insert=True)
            new_utxo[db_utxo.id] = db_utxo
            print('Added utxo: ' + db_utxo.to_json())
        
def save_address(address):
    qs = Address.objects(hash=address.hash)
    if not qs:
        _set_insert_or_update_time(address)
        address.save(force_insert=True)
        print('Added address(' + str(address.hash) + ') to database')
    elif qs[0].to_mongo() != address.to_mongo():
        qs.update(balance=address.balance, txs=address.txs, update_time=datetime.now().timestamp())
        print('Updated address(' + str(address.hash) + ') in database')
        
def create_address(addr, amount):
    json_address = json.dumps({'hash':addr, 'balance':amount, 'txs':[]})
    return Address.from_json(json_address)
    
def _after_insert_block(block, new_utxo):
    utxo = get_utxos()
    for tx in block.tx:
        _parse_inputs(tx.txid, tx.vin, utxo, new_utxo)
        _parse_outputs(tx.txid, tx.vout, utxo, new_utxo)

def save_block(block):
    if Block.objects(hash=block['hash']):
        return
    
    json_block = json.dumps(block)
    db_block = Block.from_json(json_block)
    _set_insert_or_update_time(db_block)
    db_block.save(force_insert=True)
    print('Added block(' + str(db_block.height) + ') to database')
    utxos = {}
    _after_insert_block(db_block, utxos)
    return utxos
    
def get_blockchain():
    return Block.objects()

def get_addresses():
    return Address.objects()

def get_utxos():
    return UTXO.objects()