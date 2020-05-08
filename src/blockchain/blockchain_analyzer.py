from rpc import get_hashrate
import datetime
import db
import db_queries
import time

class BlockchainAnalyzer:
    def __init__(self, blockchain=None, addresses=None, utxos=None):
        self.__blockchain = blockchain
        self.__addresses = addresses
        self.__utxos = utxos
        
    def set_blockchain(self, blockchain):
        self.__blockchain = blockchain
        
    def set_addresses(self, addresses):
        self.__addresses = addresses
        
    def set_utxos(self, utxos):
        self.__utxos = utxos
        
    def total_coin_supply(self):
        pipeline = db_queries.get_total_coin_supply_query()
        result = db.execute_query(pipeline, db.get_addresses())
        return 0 if len(result) == 0 else result[0]['total_coin_supply']
        
    def status(self):
        return {'height': self.max_block_number(), 'hashrate': get_hashrate(), 'total_coin_supply': self.total_coin_supply()}
    
    def piechart_data(self, interval, lower_height, upper_height):
        pipeline = db_queries.get_blocks_coinbase_addresses_count_query(interval, lower_height, upper_height)
        params = (interval, lower_height, upper_height)
        type = 'piechartdata'
        return db.execute_query(pipeline, self.__blockchain, type=type, params=params)
    
    def mining_difficulty(self, interval, lower_height, upper_height):
        pipeline = db_queries.get_blocks_average_difficulty_query(interval, lower_height, upper_height)
        params = (interval, lower_height, upper_height)
        type = 'miningdifficulty'
        return db.execute_query(pipeline, self.__blockchain, type=type, params=params)
    
    def transaction_volume(self, interval, lower_height, upper_height):
        pipeline = db_queries.get_transactions_average_volume_query(interval, lower_height, upper_height)
        params = (interval, lower_height, upper_height)
        type = 'transactionvolume'
        return db.execute_query(pipeline, self.__blockchain, type=type, params=params)

    def richest_wallets(self):
        addresses = self.__addresses.order_by('-balance').limit(10)
        total_coin_supply = self.total_coin_supply()
        wallets = []
        for address in addresses:
            wallets.append({'address':address.hash, 'amount':address.balance, 
                            'percentage_of_total':round(address.balance/total_coin_supply*100,2)})
        return wallets
            
    def non_empty_wallets_number(self):
        pipeline = db.queries.get_nonempty_wallets_number_query()
        return db.execute_query(pipeline, db.get_addresses())
    
    def max_block_number(self):
        pipeline = db_queries.get_highest_block_number_in_db_query()
        return db.execute_query(pipeline, self.__blockchain)[0]['height']
