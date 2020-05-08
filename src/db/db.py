from datetime import datetime
from db_utils import set_insert_or_update_time
from models import Address, Block, RequestCache, UTXO 
from mongoengine import Document
from rpc import get_block, get_block_count
import db_queries
import json

db_host = 'mongodb://mongo:27017/blockchain'
db_name = 'blockchain'

def get_blockchain():
    return Block.objects()

def get_addresses():
    return Address.objects()

def get_utxos():
    return UTXO.objects()

def get_cache():
    return RequestCache.objects()

def get_highest_block_number_in_db():
    return get_blockchain().order_by('-height').limit(1).first().height

def drop_db():
    Block.drop_collection()
    Address.drop_collection()
    UTXO.drop_collection()
    RequestCache.drop_collection()

def insert_address(address):
    query_set = Address.objects(hash=address.hash)
    if not query_set:
        set_insert_or_update_time(address)
        address.save(force_insert=True)
        print('Added address(' + str(address.hash) + ') to database')
    elif query_set[0].to_mongo() != address.to_mongo():
        set_insert_or_update_time(address, False)
        query_set.update(balance=address.balance, txs=address.txs, update_time=address.update_time)
        print('Updated address(' + str(address.hash) + ') in database')
         
def insert_block(block, new_utxos, del_utxos):
    def _parse_inputs(txid, vin, utxo, new_utxo, del_utxo):
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
                else:
                    del_utxo[db_utxo.id] = db_utxo
                db_utxo.delete()
                print('Deleted utxo: ' + db_utxo.to_json())
            except:
                exception_string = "Invalid input. txid: " + str(txid) + ", vin_txid: " + str(
                    in_txid) + ", vout: " + str(in_vout)
                raise BaseException(exception_string)
            
    def _create_utxo(tx_key, addr, amount):
        json_utxo = json.dumps({'id':tx_key, 'address':addr, 'value':amount})
        return UTXO.from_json(json_utxo)
    
    def _create_block(block):
        json_block = json.dumps(block)
        return Block.from_json(json_block)
            
    def _parse_outputs(txid, vout, utxo, new_utxo, del_utxo):
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
                set_insert_or_update_time(db_utxo)
                db_utxo.save(force_insert=True)
                new_utxo[db_utxo.id] = db_utxo
                print('Added utxo: ' + db_utxo.to_json())
        
    def _before_insert_block(block, new_utxos, del_utxos):
        utxo = get_utxos()
        for tx in block.tx:
            _parse_inputs(tx.txid, tx.vin, utxo, new_utxos, del_utxos)
            _parse_outputs(tx.txid, tx.vout, utxo, new_utxos, del_utxos)
            
    if Block.objects(hash=block['hash']):
        return
    
    db_block = _create_block(block)
    _before_insert_block(db_block, new_utxos, del_utxos)
    set_insert_or_update_time(db_block)
    db_block.save(force_insert=True)
    print('Added block(' + str(db_block.height) + ') to database')

def execute_query(pipeline, collection, **kwargs):
    def _get_from_cache_if_exists(type, params):
        cache = get_cache()
        results = cache.filter(type=type, params=params)
        
        if results.count() != 1:
            print('Invalid number of cached results: {0}'.format(results.count()))
            return None
        results.first().update(last_accessed=get_highest_block_number_in_db())
        return results.first().result
    
    def _cache_and_return_query_result(type, params, pipeline, collection):
        def _cache_request_result(type, params, result):
            json_cache = json.dumps({'type':type, 'params':params, 'result':result, 'last_accessed':get_highest_block_number_in_db()})
            cache = RequestCache.from_json(json_cache)
            cache.save()
            
        result = list(collection.aggregate(*pipeline))
        _cache_request_result(type, params, result)
        return result
    
    if 'type' not in kwargs or 'params' not in kwargs:
        return list(collection.aggregate(*pipeline))
    
    result = _get_from_cache_if_exists(kwargs['type'], kwargs['params'])
    if result:
        return result
    return _cache_and_return_query_result(kwargs['type'], kwargs['params'], pipeline, collection)
