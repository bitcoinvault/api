from rpc import get_hashrate
import time

class BlockchainParameters:
    def __init__(self):
        self.total_coin_supply = 0
        
    def calc_total_coin_supply(self, addresses):
        if addresses:
            self.total_coin_supply = addresses.sum('balance')

class BlockchainAnalyzer:
    def __init__(self, blockchain=None, addresses=None, utxos=None):
        self.__blockchain = blockchain
        self.__addresses = addresses
        self.__utxos = utxos
        self.__parameters = BlockchainParameters()
        
        self.recalculate_parameters()
        
    def recalculate_parameters(self):
        self.__parameters.calc_total_coin_supply(self.__addresses)
        
    def set_blockchain(self, blockchain):
        self.__blockchain = blockchain
        
    def set_addresses(self, addresses):
        self.__addresses = addresses
        self.__parameters.calc_total_coin_supply(self.__addresses)
        
    def set_utxos(self, utxos):
        self.__utxos = utxos
        
    def get_status(self):
        height = self.__blockchain.order_by('-height').limit(1)[0].height
        hashrate = get_hashrate()
        total_coin_supply = self.__parameters.total_coin_supply
        return {'height': height, 'hashrate': hashrate, 'total_coin_supply': total_coin_supply}

    def get_richest_wallets(self):
        addresses = self.__addresses.order_by('-balance').limit(10)
        total_coin_supply = self.__parameters.total_coin_supply
        wallets = []
        for address in addresses:
            wallets.append({'address':address.hash, 'amount':address.balance, 
                            'percentage_of_total':round(address.balance/total_coin_supply*100,2)})
        return wallets
            
