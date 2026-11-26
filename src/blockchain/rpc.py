import os
import random
import requests

rpc_user = os.getenv('RPC_USER', 'user')
rpc_pass = os.getenv('RPC_PASSWORD', 'password')
rpc_host = os.getenv('RPC_HOST', 'bvaultd')
rpc_port = os.getenv('RPC_PORT', '8332')

url = 'http://' + rpc_user + ':' + rpc_pass + '@' + rpc_host + ':' + rpc_port


def get_block_hash(block_number):
    rpc = RPC("getblockhash", [block_number])
    return rpc.execute()


def get_block(block_number, verbosity=2):
    block_hash = get_block_hash(block_number)
    rpc = RPC("getblock", [block_hash, verbosity])
    return rpc.execute()


def get_block_count():
    rpc = RPC("getblockcount")
    return rpc.execute()


def decode_raw_transaction(raw_hex):
    # Ask the same Core/network to derive outpoints, amounts and destination addresses.
    try:
        response = RPC("decoderawtransaction", [raw_hex]).post(timeout=(3, 10), allow_redirects=False)
        payload = response.json() if response.status_code == 200 else None
    except (requests.exceptions.RequestException, ValueError):
        payload = None
    if (not isinstance(payload, dict) or payload.get('error') is not None
            or not isinstance(payload.get('result'), dict)):
        raise ValueError('Core could not decode the serialized vault transaction')
    return payload['result']


def get_hashrate():
    rpc = RPC("getnetworkhashps")
    return rpc.execute()


class RPC:
    def __init__(self, method="", params=[], rpc_id=random.random()):
        self.method = method
        self.params = params
        self.jsonrpc = "2.0"
        self.id = rpc_id

    def post(self, **options):
        return requests.post(url, json=self.dict(), **options)

    def execute(self):
        return self.post().json()['result']

    def dict(self):
        payload = {
            "method": self.method,
            "params": self.params,
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        return payload
