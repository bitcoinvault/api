import logging
import os

from mongoengine.errors import NotUniqueError
from pymongo.errors import OperationFailure

from db.db_utils import create_address, create_block, create_utxo, set_insert_or_update_time
from db.models import Address, Block, RequestCache, UTXO
from db import query_cache
from blockchain.rpc import RPCError, get_raw_transaction

# logging.basicConfig(level=logging.DEBUG, filename="blockchain.log", format='%(asctime)s %(levelname)-8s %(message)s',
#                     datefmt='%d-%m-%Y %H:%M:%S')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%d-%m-%Y %H:%M:%S',
    handlers=[
        logging.StreamHandler()
    ]
)

db_prot = os.getenv('DB_PROTOCOL', 'mongodb')
db_name = os.getenv('DB_NAME', 'blockchain')
db_host = os.getenv('DB_URL', 'mongo')
db_port = os.getenv('DB_PORT', '27017')
db_user = os.getenv('DB_USERNAME', '')
db_pass = os.getenv('DB_PASSWORD', '')
db_para = os.getenv('DB_URI_PARAMS', '')


def get_db_uri():
    uri = db_prot + '://'
    if db_user != '' and db_pass != '':
        uri += db_user + ':' + db_pass + '@'
    uri += db_host + ':' + db_port + '/' + db_name
    if db_para != '':
        uri += db_para
    return uri


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


def insert_address(address):
    query_set = Address.objects(hash=address.hash)
    if not query_set:
        set_insert_or_update_time(address)
        try:
            address.save(force_insert=True)
            logging.debug('Added address(' + str(address.hash) + ') to database')
        except NotUniqueError as e:
            logging.warning(e)
            raise
    elif query_set[0].to_mongo() != address.to_mongo():
        set_insert_or_update_time(address, False)
        query_set.update(balance=address.balance, txs=address.txs, update_time=address.update_time)
        logging.debug('Updated address(' + str(address.hash) + ') in database')


def insert_block(block, new_utxos, del_utxos):
    previous_transactions = {}

    def _is_untracked_output(txid, output_number):
        if txid not in previous_transactions:
            previous_transactions[txid] = get_raw_transaction(txid)
        transaction = previous_transactions[txid]
        if not isinstance(transaction, dict) or transaction.get('txid') != txid:
            raise RPCError('RPC could not verify the missing input transaction')
        outputs = transaction.get('vout')
        if not isinstance(outputs, list):
            raise RPCError('RPC returned invalid previous outputs')
        matches = [output for output in outputs
                   if isinstance(output, dict) and output.get('n') == output_number]
        if len(matches) != 1:
            raise RPCError('RPC could not verify the missing previous output')
        script = matches[0].get('scriptPubKey')
        if not isinstance(script, dict):
            raise RPCError('RPC returned an invalid previous output script')
        if script.get('addresses') or 'address' in script:
            return False
        if (not isinstance(script.get('addresses', []), list) or
                not isinstance(script.get('hex'), str) or
                not isinstance(script.get('type'), str) or not script['type']):
            raise RPCError('RPC did not prove the previous output was untracked')
        return True

    def _parse_inputs(txid, vin, utxo, new_utxo, del_utxo):
        for inp in vin:
            if 'coinbase' in inp:
                continue
            in_txid = inp.txid
            in_vout = inp.vout
            tx_key = in_txid + str(in_vout)
            try:
                db_utxo = utxo.get(id=tx_key)
            except UTXO.DoesNotExist:
                if _is_untracked_output(in_txid, in_vout):
                    logging.info('Skipping verified untracked input %s:%s', in_txid, in_vout)
                    continue
                raise
            try:
                if db_utxo.id in new_utxo:
                    del new_utxo[db_utxo.id]
                else:
                    del_utxo[db_utxo.id] = db_utxo
                db_utxo.delete()
                logging.debug('Deleted utxo: ' + db_utxo.to_json())
            except Exception as e:
                logging.warning(e)
                raise

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
                    logging.warning(e)
                    raise
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
        logging.warning(e)
        raise


def execute_query(pipeline, collection, **kwargs):
    if not collection or collection == None:
        if 'default' in kwargs:
            return kwargs['default']
        raise Exception('execute_query failed: performed on empty collection without default value specified')

    try:
        if 'type' not in kwargs or 'params' not in kwargs:
            return list(collection.aggregate(*pipeline))
        return query_cache.execute(pipeline, collection, kwargs['type'], kwargs['params'])
    except OperationFailure as e:
        if 'default' in kwargs:
            return kwargs['default']
        raise
