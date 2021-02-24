import json
import logging
from datetime import datetime

from models import Address, Block, UTXO

logging.basicConfig(level=logging.DEBUG, filename="blockchain.log", format='%(asctime)s %(levelname)-8s %(message)s',
                    datefmt='%d-%m-%Y %H:%M:%S')


def set_insert_or_update_time(db_object, ins=True, upd=True):
    time = datetime.now().timestamp()

    if ins: db_object.insert_time = time
    if upd: db_object.update_time = time


def create_address(addr, amount):
    json_address = json.dumps({'hash': addr, 'balance': amount, 'txs': []})
    logging.debug(json_address)
    return Address.from_json(json_address)


def create_block(rpc_block):
    def strip_block(block):
        for key in ('confirmations', 'nonce', 'size', 'bits', 'strippedsize', 'versionHex', 'chainwork', 'merkleroot',
                    'alertmerkleroot',
                    'mediantime', 'version', 'weight', 'nTx', 'auxheader'):
            block.pop(key, None)
        return block

    def strip_tx(tx):
        for key in ('version', 'size', 'vsize', 'weight', 'locktime', 'hex'):
            tx.pop(key, None)
        return tx

    def strip_vin(vin):
        for key in ('txinwitness', 'sequence', 'scriptSig'):
            vin.pop(key, None)
        return vin

    def strip_scriptPubKey(script):
        for key in ('asm', 'hex', 'reqSigs', 'type'):
            script.pop(key, None)
        return script

    rpc_block = strip_block(rpc_block)
    if 'atx' not in rpc_block:
        rpc_block['atx'] = []

    for tx in rpc_block['tx']:
        tx = strip_tx(tx)

        for vin in tx['vin']:
            vin = strip_vin(vin)
        for vout in tx['vout']:
            vout['scriptPubKey'] = strip_scriptPubKey(vout['scriptPubKey'])

    for atx in rpc_block['atx']:
        atx = strip_tx(atx)

        for vin in atx['vin']:
            vin = strip_vin(vin)
        for vout in atx['vout']:
            vout['scriptPubKey'] = strip_scriptPubKey(vout['scriptPubKey'])

    json_block = json.dumps(rpc_block)
    logging.debug(json_block)
    return Block.from_json(json_block)


def create_utxo(tx_key, addr, amount):
    json_utxo = json.dumps({'id': tx_key, 'address': addr, 'value': amount})
    logging.debug(json_utxo)
    return UTXO.from_json(json_utxo)
