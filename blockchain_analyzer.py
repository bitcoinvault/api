import copy

import requests
import json
import random

url = "http://user:password@localhost:8332"


def execute_rpc(rpc):
    rpc = rpc.dict()
    return requests.post(url, json=rpc).json()['result']


def get_block_hash(block_number):
    rpc = RPC("getblockhash", [block_number])
    block_hash = execute_rpc(rpc)
    return block_hash


def get_block(block_number, verbosity=2):
    block_hash = get_block_hash(block_number)
    rpc = RPC("getblock", [block_hash, verbosity])
    block = execute_rpc(rpc)
    return block


def get_block_count():
    rpc = RPC("getblockcount")
    return execute_rpc(rpc)


def get_hashrate():
    rpc = RPC("getnetworkhashps")
    return execute_rpc(rpc)


def save_json(in_dict, name):
    dict_json = json.dumps(in_dict)
    with open(name, "w") as f:
        f.write(dict_json)


def load_file(name):
    with open(name, 'r') as f:
        res = json.load(f)
    return res


class RPC:
    def __init__(self, method="", params=[], rpc_id=random.random()):
        self.method = method
        self.params = params
        self.jsonrpc = "2.0"
        self.id = rpc_id

    def dict(self):
        payload = {
            "method": self.method,
            "params": self.params,
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        return payload


class BlockchainAnalyzer:
    def __init__(self):
        try:
            self.__height_last_update = load_file('height.json')['height']
            self.__utxo = load_file('utxo.json')
        except IOError:
            self.__height_last_update = 0
            self.__utxo = {}
        self.__ten_richest_wallets = []
        self.__hashrate = 0
        self.__total_coin_supply = 0

    def __save_data_to_files(self, height):
        save_json({'height': height}, 'height.json')
        save_json(self.__utxo, 'utxo.json')

    def __parse_tx(self, txs):
        for tx in txs:
            txid = tx['txid']
            vin = tx['vin']
            vout = tx['vout']
            for inp in vin:
                if 'coinbase' in inp:
                    continue
                in_txid = inp['txid']
                in_vout = inp['vout']
                tx_key = in_txid + str(in_vout)
                if tx_key in self.__utxo:
                    del self.__utxo[tx_key]
                else:
                    exception_string = "Invalid input. txid: " + str(txid) + ", vin_txid: " + str(
                        in_txid) + ", vout: " + str(in_vout)
                    raise BaseException(exception_string)
            for out in vout:
                if 'addresses' not in out['scriptPubKey']:
                    continue
                out_addr = out['scriptPubKey']['addresses']
                out_addr_len = len(out_addr)
                if out_addr_len != 1:
                    exception_string = "Bad address length: " + out_addr_len + " txid: " + str(txid)
                    raise BaseException(exception_string)
                out_n = out['n']
                tx_key = txid + str(out_n)
                self.__utxo[tx_key] = {'addr': out_addr[0], 'amount': out['value']}

    def update_stats(self):
        addr_balance = dict()
        height = get_block_count()
        self.__hashrate = get_hashrate()
        if self.__height_last_update < height:
            for i in range(self.__height_last_update + 1, height + 1):
                block = get_block(i)
                self.__parse_tx(block['tx'])
            self.__height_last_update = height
        for tx in self.__utxo.values():
            if tx['addr'] not in addr_balance:
                addr_balance[tx['addr']] = 0
            addr_balance[tx['addr']] += tx['amount']
        ten_richest_addr = sorted(addr_balance.items(), key=lambda kv: (kv[1], kv[0]))[-10:]
        self.__total_coin_supply = sum(addr_balance.values())
        self.__ten_richest_wallets = []
        for i in reversed(range(10)):
            self.__ten_richest_wallets.append({'address': ten_richest_addr[i][0], 'amount': ten_richest_addr[i][1],
                                               'percentage_of_total': round(ten_richest_addr[i][1]
                                                                            / self.__total_coin_supply * 100, 2)})
        self.__save_data_to_files(height)

    def get_status(self):
        return {'height': self.__height_last_update, 'hashrate': self.__hashrate,
                'total_coin_supply': self.__total_coin_supply}

    def get_richest_wallets(self):
        return copy.deepcopy(self.__ten_richest_wallets)
