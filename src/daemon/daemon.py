from db import db_host, db_name, drop_db, execute_query, get_address, get_blockchain, get_utxos, insert_address, insert_block
from db_queries import get_highest_block_number_in_db_query
from db_utils import create_address
from mongoengine import connect
from rpc import get_block, get_block_count
import sys, time
        
def update_addresses(new_utxos, del_utxos):
    addresses = {}
    for db_utxo in new_utxos.values():
        txid = db_utxo.id[:-1]
        addr = db_utxo.address
        amount = db_utxo.value
        if addr not in addresses:
            addresses[addr] = get_address(addr)
        addresses[addr].balance += amount
        
        if txid not in addresses[addr].txs:
            addresses[addr].txs.append(txid)
            
    for db_utxo in del_utxos.values():
        txid = db_utxo.id[:-1]
        addr = db_utxo.address
        amount = db_utxo.value
        if addr in addresses:
            addresses[addr].balance -= amount
            
    for address in addresses.values():
        insert_address(address)
        
def update_blockchain():
    def _highest_block_number():
        pipeline = get_highest_block_number_in_db_query()
        result = execute_query(pipeline, get_blockchain())
        return result[0]['height'] if len(result) > 0 else -1
    
    start_block_number = _highest_block_number() + 1
    end_block_number = get_block_count() + 1
    new_utxos = {}
    del_utxos = {}
    
    for idx in range(start_block_number, end_block_number):
        block = get_block(idx)
        n_utxos = {}
        d_utxos = {}
        insert_block(block, n_utxos, d_utxos)
        new_utxos = {**new_utxos, **n_utxos}
        del_utxos = {**del_utxos, **d_utxos}
    update_addresses(new_utxos, del_utxos)

if __name__ == '__main__':
    connect(db_name, host=db_host)
    drop_db()
    while True:
        update_blockchain()
        sys.stdout.flush()
        sys.stderr.flush()
        time.sleep(5)
