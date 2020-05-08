from db import create_address, db_host, db_name, drop_db, get_highest_block_number_in_db, save_address, save_block
from mongoengine import connect
from rpc import get_block, get_block_count
import sys
        
def update_addresses(new_utxos, del_utxos):
    addresses = {}
    for db_utxo in new_utxos.values():
        txid = db_utxo.id[:-1]
        addr = db_utxo.address
        amount = db_utxo.value
        if addr not in addresses:
            addresses[addr] = create_address(addr, amount)
        addresses[addr].balance += amount
        
        if txid not in addresses[addr].txs:
            addresses[addr].txs.append(txid)
            
    for db_utxo in del_utxos.values():
        txid = db_utxo.id[:-1]
        addr = db_utxo.address
        amount = db_utxo.value
        addresses[addr].balance -= amount
        
        if txid in addresses[addr].txs:
            addresses[addr].txs.remove(txid)
            
    for address in addresses.values():
        save_address(address)
        
def update_blockchain():
    start_block_number = get_highest_block_number_in_db() + 1
    end_block_number = get_block_count() + 1
    new_utxos = {}
    del_utxos = {}
    
    for idx in range(start_block_number, end_block_number):
        block = get_block(idx)
        n_utxos = {}
        d_utxos = {}
        save_block(block, n_utxos, d_utxos)
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