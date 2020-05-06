import requests
import json
import random

url = "http://user:password@bitcoind:8332"


def execute_rpc(rpc):
    rpc = rpc.dict()
    response = requests.post(url, json=rpc).json()
    return response['result']


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