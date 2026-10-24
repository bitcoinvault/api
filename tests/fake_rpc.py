"""Local-only RPC stub for the application-image HTTP smoke test."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


def transaction(txid, inputs, outputs):
    return {'txid': txid, 'hash': txid, 'vin': inputs, 'vout': outputs}


ORIGIN = transaction('origin', [{'coinbase': '00'}], [
    {'n': 0, 'value': 2, 'scriptPubKey': {'addresses': ['sender'], 'hex': '51', 'type': 'pubkeyhash'}},
    {'n': 1, 'value': 1, 'scriptPubKey': {'hex': '51', 'type': 'nonstandard'}},
])
SPEND = transaction('spend', [{'txid': 'origin', 'vout': 0}, {'txid': 'origin', 'vout': 1}], [
    {'n': 0, 'value': 3, 'scriptPubKey': {'addresses': ['recipient'], 'hex': '51', 'type': 'pubkeyhash'}},
])
BLOCKS = [
    {'hash': 'block-0', 'height': 0, 'time': 1, 'difficulty': 1, 'tx': [ORIGIN]},
    {'hash': 'block-1', 'height': 1, 'previousblockhash': 'block-0',
     'time': 2, 'difficulty': 1, 'tx': [SPEND]},
]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        method, params = payload.get('method'), payload.get('params', [])
        if method == 'getnetworkhashps':
            result = 123
        elif method == 'getblockcount':
            result = 1
        elif method == 'getblockhash' and len(params) == 1 and params[0] in (0, 1):
            result = BLOCKS[params[0]]['hash']
        elif method == 'getblock' and params and params[0] in ('block-0', 'block-1'):
            result = BLOCKS[int(params[0][-1])]
        elif method == 'getrawtransaction' and params and params[0] in ('origin', 'spend'):
            result = ORIGIN if params[0] == 'origin' else SPEND
        else:
            self.send_error(400)
            return
        body = json.dumps({'result': result, 'error': None, 'id': payload.get('id')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 8332), Handler).serve_forever()
