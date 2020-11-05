from blockchain_analyzer import BlockchainAnalyzer
from db import db_host, db_name, drop_db, execute_query, get_address, get_addresses, get_blockchain, get_highest_block_number_in_db, \
               get_utxos, insert_address, insert_block
from db_utils import create_address
from decimal import Decimal
from mongobackup import backup, restore
from mongoengine import connect
from rpc import get_block, get_block_count
import logging, os, shutil, sys, time
logging.basicConfig(level=logging.DEBUG, filename="daemon.log", format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%d-%m-%Y %H:%M:%S')

analyzer = BlockchainAnalyzer()
BACKUP_FEATURE_HEIGHT = 57000

period_block_map = {
    'hour' : 6,
    'day' : 144,
    'week' : 1008,
    'month' : 4320
}

def _crop_params(interval, lower_height, upper_height):
    if interval < 1: interval = 1
    elif interval > 4320: interval = 4320
    
    if lower_height < 1: lower_height = 1
    elif lower_height > analyzer.max_block_number(): lower_height = analyzer.max_block_number()
    
    if upper_height < lower_height: upper_height = lower_height
    elif upper_height > analyzer.max_block_number(): upper_height = analyzer.max_block_number()
    
    return interval, lower_height, upper_height

def period_to_block_range_and_interval(period):
    upper_height = analyzer.max_block_number()
    
    if period == 'week': return _crop_params(period_block_map['hour'], upper_height - period_block_map['week'], upper_height)
    if period == 'month' : return _crop_params(period_block_map['day'], upper_height - period_block_map['month'], upper_height)
    
    return _crop_params(period_block_map['week'], 1, upper_height)

        
def update_addresses(new_utxos, del_utxos):
    def _update_balance(db_utxo, addresses, new_utxo=True):
        addr = db_utxo.address
        amount = Decimal(db_utxo.value)
        if addr not in addresses:
            addresses[addr] = get_address(addr)
        
        if new_utxo:
            addresses[addr].balance += amount
        else:
            addresses[addr].balance -= amount
            
    addresses = {}
    for db_utxo in new_utxos.values():
        _update_balance(db_utxo, addresses)
        
        txid = db_utxo.id[:-1]
        addr = db_utxo.address
        if txid not in addresses[addr].txs:
            addresses[addr].txs.append(txid)
            
    for db_utxo in del_utxos.values():
        _update_balance(db_utxo, addresses, False)
            
    for address in addresses.values():
        insert_address(address)
    
def update_analyzer():
    analyzer.set_blockchain(get_blockchain())
    analyzer.set_addresses(get_addresses())
    analyzer.set_utxos(get_utxos())
        
def restore_db(reorg=False):
    def _last_backup():
        filename = os.popen('ls -ltr /var/backups/mongodb | tail -n 1 | egrep "backup.*" -o').readline()
        if filename:
            return '/var/backups/mongodb/' + filename.replace('\n', '')
        return None
    
    try:
        file_to_restore = _last_backup()
        
        if file_to_restore:
            drop_db()
            logging.debug("File to restore: " + file_to_restore)
            restore('""', '""', file_to_restore, backup_directory_output_path='/tmp/mongo_dump_restore')
            logging.info("DB restored to height: {}".format(get_highest_block_number_in_db()))
        elif not reorg:
            logging.warn("No backup to restore")
            backup('""', '""', "/var/backups/mongodb/", mongo_backup_directory_path='/tmp/mongo_dump_backup', purge_local=30)
            logging.info("Initial database state dumped at height: {}".format(get_highest_block_number_in_db()))
        else:
            logging.warn("No backup to restore and chain reorg occured. Need to fill DB from scratch.")
            drop_db()
    except Exception as e:
        logging.warn(e)
    
    update_analyzer()

def backup_db():
    current_height = analyzer.max_block_number()
    if current_height < BACKUP_FEATURE_HEIGHT:
        return
    
    if current_height % 1000 == 0: # dump db every 1000th block
        try:
            logging.info("Doing backup at height: {}".format(current_height))
            backup('""', '""', "/var/backups/mongodb/", mongo_backup_directory_path='/tmp/mongo_dump_backup', purge_local=30)
        except Exception as e:
            logging.warn(e)
            
def check_reorg():
    current_height = analyzer.max_block_number()
    if current_height < BACKUP_FEATURE_HEIGHT:
        return False
    
    reorg = not analyzer.check_chain()
    if reorg:
        restore_db(True)
        
    return reorg
    
def update_blockchain():
    def _reexecute_queries():
        analyzer.piechart_data(100, 1, analyzer.max_block_number())
        periods = ['week', 'month', 'all']
        for period in periods:
            interval, lower_height, upper_height = period_to_block_range_and_interval(period)
            analyzer.mining_difficulty(interval, lower_height, upper_height)
            analyzer.transaction_volume(interval, lower_height, upper_height)
    
    start_block_number = analyzer.max_block_number() + 1
    end_block_number = get_block_count() + 1
    new_utxos = {}
    del_utxos = {}
    
    try:
        for idx in range(start_block_number, end_block_number):
            logging.debug("Processing block = {}".format(idx))
            
            block = get_block(idx)
            n_utxos = {}
            d_utxos = {}
            
            insert_block(block, n_utxos, d_utxos)
            new_utxos = {**new_utxos, **n_utxos}
            del_utxos = {**del_utxos, **d_utxos}
            update_analyzer()
            
            if check_reorg():
                return
            backup_db()
        update_addresses(new_utxos, del_utxos)
    except Exception as e:
        logging.error(e)
        raise
    
    if start_block_number != end_block_number:
        _reexecute_queries()

def init_cleanup():
    shutil.rmtree('/tmp/mongo_dump_backup', True)
    shutil.rmtree('/tmp/mongo_dump_restore', True)

def try_to_connect():
    try:
        connect(db_name, host=db_host)
    except Exception as e:
        logging.error(e)
        raise
    
if __name__ == '__main__':
    init_cleanup()
    try_to_connect()
    restore_db()
    while True:
        update_blockchain()
        time.sleep(5)
