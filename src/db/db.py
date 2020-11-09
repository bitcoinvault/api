from datetime import datetime
from db_utils import create_address, create_block, create_utxo, set_insert_or_update_time
from models import Address, Block, RequestCache, UTXO 
from mongoengine import Document
from mongoengine.errors import NotUniqueError
from pymongo.errors import OperationFailure
from rpc import get_block, get_block_count
import db_queries, json, logging
logging.basicConfig(level=logging.DEBUG, filename="blockchain.log", format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%d-%m-%Y %H:%M:%S')

db_host = 'mongodb://mongo:27017/blockchain'
db_name = 'blockchain'

def get_blockchain():
    return Block.objects()

def get_addresses():
    return Address.objects()

def get_address(addr):
    return Address.objects(hash=addr).first()

def get_utxos():
    return UTXO.objects()

def get_cache():
    return RequestCache.objects()

def get_highest_block_number_in_db():
    try:
        return get_blockchain().order_by('-height').limit(1).first().height
    except:
        return -1
  
def drop_db():
    Block.drop_collection()
    Address.drop_collection()
    UTXO.drop_collection()
    RequestCache.drop_collection()

def insert_address(address):
    query_set = Address.objects(hash=address.hash)
    if not query_set:
        set_insert_or_update_time(address)
        try:
            address.save(force_insert=True)
            logging.debug('Added address(' + str(address.hash) + ') to database')
        except NotUniqueError as e:
            logging.warn(e)
    elif query_set[0].to_mongo() != address.to_mongo():
        set_insert_or_update_time(address, False)
        query_set.update(balance=address.balance, txs=address.txs, update_time=address.update_time)
        logging.debug('Updated address(' + str(address.hash) + ') in database')
         
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
                logging.debug('Deleted utxo: ' + db_utxo.to_json())
            except Exception as e:
                logging.warn(e)
            
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
            db_utxo = create_utxo(tx_key, out_addr, out.value)
            if not utxo(id=db_utxo.id):
                set_insert_or_update_time(db_utxo)
                try:
                    db_utxo.save(force_insert=True)
                    new_utxo[db_utxo.id] = db_utxo
                    logging.debug('Added utxo: ' + db_utxo.to_json())
                except NotUniqueError as e:
                    logging.warn(e)
            if not get_address(out_addr):
                address = create_address(out_addr, 0)
                insert_address(address)
        
    def _before_insert_block(block, new_utxos, del_utxos):
        utxo = get_utxos()
        for tx in block.tx:
            _parse_inputs(tx.txid, tx.vin, utxo, new_utxos, del_utxos)
            _parse_outputs(tx.txid, tx.vout, utxo, new_utxos, del_utxos)
            
    if Block.objects(hash=block['hash']):
        return
    
    db_block = create_block(block)
    _before_insert_block(db_block, new_utxos, del_utxos)
    set_insert_or_update_time(db_block)
    try:
        db_block.save(force_insert=True)
        logging.debug('Added block(' + str(db_block.height) + ') to database')
    except NotUniqueError as e:
        logging.warn(e)

def execute_query(pipeline, collection, **kwargs):
    def _get_from_cache_if_exists(type, params):
        cache = get_cache()
        results = cache.filter(type=type, params=params)
        
        if results.count() != 1:
            logging.warn('Invalid number of cached results: {0}'.format(results.count()))
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
    
    if not collection or collection == None:
        if 'default' in kwargs:
            return kwargs['default']
        raise Exception('execute_query failed: performed on empty collection without default value specified')
    
    try:
        if 'type' not in kwargs or 'params' not in kwargs:
            return list(collection.aggregate(*pipeline))
    
        result = _get_from_cache_if_exists(kwargs['type'], kwargs['params'])
        if result:
            return result
        return _cache_and_return_query_result(kwargs['type'], kwargs['params'], pipeline, collection)
    except OperationFailure as e:
        if 'default' in kwargs:
            return kwargs['default']
        raise