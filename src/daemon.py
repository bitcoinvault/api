import logging
from decimal import Decimal
from time import sleep

from mongoengine import connect

from blockchain.blockchain_analyzer import BlockchainAnalyzer
from blockchain.rpc import RPCError, get_block, get_block_count, get_block_hash
from db.db import get_db_uri, get_address, get_addresses, get_blockchain, get_utxos, insert_address, insert_block
from db import query_cache

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


def _verify_stored_tip(node_height):
    tip = get_blockchain().only('hash', 'height').order_by('-height').first()
    if type(node_height) is not int or node_height < 0:
        raise RPCError('RPC returned an invalid chain height')
    if tip is None:
        return None
    if tip.height > node_height:
        raise RPCError('RPC node is behind the stored tip; waiting for synchronization')
    node_hash = get_block_hash(tip.height)
    if not isinstance(node_hash, str) or not node_hash:
        raise RPCError('RPC returned an invalid tip hash')
    if node_hash != tip.hash:
        query_cache.invalidate()
        raise RuntimeError('Stored blockchain tip is not canonical; verified recovery required')
    return tip


def _verify_completed_update(height, block_hash):
    tip = _verify_stored_tip(get_block_count())
    if tip is None or tip.height != height or tip.hash != block_hash:
        query_cache.invalidate()
        raise RuntimeError('Stored tip changed before canonical verification; verified recovery required')


def update_blockchain():
    def _reexecute_queries():
        analyzer.piechart_data(100, 1, analyzer.max_block_number())
        periods = ['week', 'month', 'all']
        for period in periods:
            interval, lower_height, upper_height = period_to_block_range_and_interval(period)
            analyzer.mining_difficulty(interval, lower_height, upper_height)
            analyzer.transaction_volume(interval, lower_height, upper_height)

    pending = query_cache.get_pending_verification()
    if pending is not None:
        _verify_completed_update(pending.verification_height, pending.verification_hash)
        query_cache.complete_update(pending.generation)
        _reexecute_queries()
        return

    query_cache.assert_no_pending_update()
    start_block_number = analyzer.max_block_number() + 1
    end_block_number = get_block_count() + 1
    tip = _verify_stored_tip(end_block_number - 1)
    if start_block_number == end_block_number:
        return

    generation = None
    previous_hash = tip.hash if tip is not None else None
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
                if (block.get('height') != idx or
                        (previous_hash is not None and block.get('previousblockhash') != previous_hash)):
                    query_cache.invalidate()
                    raise RuntimeError('Fetched block does not extend the canonical chain; verified recovery required')
                n_utxos = {}
                d_utxos = {}

                if generation is None:
                    generation = query_cache.begin_update()
                insert_block(block, n_utxos, d_utxos)
                previous_hash = block['hash']
                new_utxos = {**new_utxos, **n_utxos}
                del_utxos = {**del_utxos, **d_utxos}
                update_analyzer()

            update_addresses(new_utxos, del_utxos)

    except Exception as e:
        logging.error(e)
        raise

    query_cache.mark_writes_complete(generation, end_block_number - 1, previous_hash)
    _verify_completed_update(end_block_number - 1, previous_hash)
    query_cache.complete_update(generation)
    _reexecute_queries()


def try_to_connect():
    try:
        connect(host=get_db_uri())
    except Exception as e:
        logging.error(e)
        raise


def run():
    try_to_connect()
    update_analyzer()
    while True:
        try:
            update_blockchain()
        except RPCError as error:
            if query_cache.get_pending_verification() is None:
                query_cache.assert_no_pending_update()
            logging.warning('%s; retrying after the polling interval', error)
        sleep(5)


if __name__ == '__main__':
    run()
