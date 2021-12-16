import logging
from decimal import Decimal
from time import sleep

from mongoengine import connect

from blockchain.blockchain_analyzer import BlockchainAnalyzer
from blockchain.rpc import get_block, get_block_count
from db.db import get_db_uri, get_address, get_addresses, get_blockchain, get_utxos, insert_address, insert_block

# logging.basicConfig(level=logging.DEBUG, filename="daemon.log", format='%(asctime)s %(levelname)-8s %(message)s',
#                     datefmt='%d-%m-%Y %H:%M:%S')

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)-8s %(message)s',
    datefmt='%d-%m-%Y %H:%M:%S',
    handlers=[
        logging.StreamHandler()
    ]
)

analyzer = BlockchainAnalyzer()
MAX_CHUNK_SIZE = 2000

period_block_map = {
    'hour': 6,
    'day': 144,
    'week': 1008,
    'month': 4320
}


def _crop_params(interval, lower_height, upper_height):
    if interval < 1:
        interval = 1
    elif interval > 4320:
        interval = 4320

    if lower_height < 1:
        lower_height = 1
    elif lower_height > analyzer.max_block_number():
        lower_height = analyzer.max_block_number()

    if upper_height < lower_height:
        upper_height = lower_height
    elif upper_height > analyzer.max_block_number():
        upper_height = analyzer.max_block_number()

    return interval, lower_height, upper_height


def period_to_block_range_and_interval(period):
    upper_height = analyzer.max_block_number()

    if period == 'week': return _crop_params(period_block_map['hour'], upper_height - period_block_map['week'],
                                             upper_height)
    if period == 'month': return _crop_params(period_block_map['day'], upper_height - period_block_map['month'],
                                              upper_height)

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

    for db_utxo in del_utxos.values():
        _update_balance(db_utxo, addresses, False)

    for address in addresses.values():
        insert_address(address)


def update_analyzer():
    analyzer.set_blockchain(get_blockchain())
    analyzer.set_addresses(get_addresses())
    analyzer.set_utxos(get_utxos())


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
    chunk_size = end_block_number - start_block_number
    iterations = chunk_size // MAX_CHUNK_SIZE + 1

    try:
        for i in range(iterations):
            logging.debug("Processing chunk: {}/{}".format(i + 1, iterations))
            new_utxos = {}
            del_utxos = {}
            new_start_block_number = start_block_number + i * MAX_CHUNK_SIZE
            new_end_block_number = min(new_start_block_number + MAX_CHUNK_SIZE, end_block_number)

            for idx in range(new_start_block_number, new_end_block_number):
                logging.debug("Processing block = {}".format(idx))

                block = get_block(idx)
                n_utxos = {}
                d_utxos = {}

                insert_block(block, n_utxos, d_utxos)
                new_utxos = {**new_utxos, **n_utxos}
                del_utxos = {**del_utxos, **d_utxos}
                update_analyzer()

            update_addresses(new_utxos, del_utxos)

    except Exception as e:
        logging.error(e)
        raise

    if start_block_number != end_block_number:
        _reexecute_queries()


def try_to_connect():
    try:
        connect(host=get_db_uri())
    except Exception as e:
        logging.error(e)
        raise


if __name__ == '__main__':
    try_to_connect()
    update_analyzer()
    while True:
        update_blockchain()
        sleep(5)
