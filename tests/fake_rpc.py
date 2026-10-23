"""Local-only RPC stub for the production-image HTTP smoke test."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if payload.get('method') != 'getnetworkhashps':
            self.send_error(400)
            return
        body = json.dumps({'result': 123, 'error': None, 'id': payload.get('id')}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    HTTPServer(('0.0.0.0', 8332), Handler).serve_forever()
