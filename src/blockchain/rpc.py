import os
import random
import requests

rpc_user = os.getenv('RPC_USER', 'user')
rpc_pass = os.getenv('RPC_PASSWORD', 'password')
rpc_host = os.getenv('RPC_HOST', 'bvaultd')
rpc_port = os.getenv('RPC_PORT', '8332')

url = 'http://' + rpc_user + ':' + rpc_pass + '@' + rpc_host + ':' + rpc_port


class RPCError(RuntimeError):
    pass


def get_block_hash(block_number):
    rpc = RPC("getblockhash", [block_number])
    result = rpc.execute()
    if not isinstance(result, str) or not result:
        raise RPCError('RPC getblockhash returned an invalid hash')
    return result


def get_block(block_number, verbosity=2):
    block_hash = get_block_hash(block_number)
    rpc = RPC("getblock", [block_hash, verbosity])
    result = rpc.execute()
    if not isinstance(result, dict):
        raise RPCError('RPC getblock returned an invalid block')
    return result


def get_block_count():
    rpc = RPC("getblockcount")
    result = rpc.execute()
    if type(result) is not int or result < 0:
        raise RPCError('RPC getblockcount returned an invalid height')
    return result


def get_raw_transaction(txid):
    return RPC('getrawtransaction', [txid, True]).execute()


def get_hashrate():
    rpc = RPC("getnetworkhashps")
    return rpc.execute()


class RPC:
    def __init__(self, method="", params=[], rpc_id=random.random()):
        self.method = method
        self.params = params
        self.jsonrpc = "2.0"
        self.id = rpc_id

    def execute(self):
        try:
            response = requests.post(url, json=self.dict(), timeout=(5, 30))
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            raise RPCError('RPC request failed: ' + self.method) from None
        if (not isinstance(payload, dict) or payload.get('error') is not None or
                payload.get('result') is None):
            raise RPCError('RPC returned an error or invalid result: ' + self.method)
        return payload['result']

    def dict(self):
        payload = {
            "method": self.method,
            "params": self.params,
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        return payload
