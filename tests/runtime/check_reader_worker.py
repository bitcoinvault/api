"""Run inside the release runtime: python -B /tests/runtime/check_reader_worker.py."""

import http.client
import json
import os
import signal
import socket
import subprocess
import time
import unittest
from contextlib import closing

import gunicorn


LOOPBACK = '127.0.0.1'
RESPONSE_LIMIT = 12  # Seconds, comfortably below Gunicorn's default 30s worker timeout.


class ReaderWorkerTest(unittest.TestCase):
    def test_unavailable_mongo_keeps_sync_worker_serving(self):
        self.assertEqual(gunicorn.__version__, '20.0.4')
        with socket.socket() as mongo, socket.socket() as listener:
            mongo.bind((LOOPBACK, 0))  # Reserve the port without ever listening on it.
            listener.bind((LOOPBACK, 0))
            listener.listen()
            fd = listener.fileno()
            master = subprocess.Popen(
                ['gunicorn', '--workers', '1', '--worker-class', 'sync', '--bind', f'fd://{fd}',
                 '--worker-tmp-dir', '/dev/shm', '--access-logfile', '-',
                 '--access-logformat', 'served %(p)s %(U)s %(s)s', 'api:app'],
                cwd='/app', pass_fds=(fd,), start_new_session=True, stdout=subprocess.PIPE,
                text=True, env={'PATH': os.environ['PATH'], 'PYTHONDONTWRITEBYTECODE': '1',
                                'API_BLOCK_INDEX_MODE': 'verify', 'DB_URL': LOOPBACK,
                                'DB_PORT': str(mongo.getsockname()[1])})
            try:
                port = listener.getsockname()[1]
                listener.close()  # Only Gunicorn should own the listening socket now.
                for path in ('/status', '/miningdifficulty'):
                    with closing(http.client.HTTPConnection(LOOPBACK, port, timeout=RESPONSE_LIMIT)) as conn:
                        start = time.monotonic()
                        conn.request('GET', path)
                        response = conn.getresponse()
                        body = response.read()
                        elapsed = time.monotonic() - start
                        self.assertLess(elapsed, RESPONSE_LIMIT, path)
                        self.assertEqual(response.status, 503, path)
                        self.assertEqual(response.headers.get_content_type(), 'application/json')
                        self.assertEqual(json.loads(body), {'error': 'Cannot verify the block height index'})
                        print(f'{path}: 503 JSON in {elapsed:.2f}s', flush=True)
            finally:
                master.terminate()  # Let the master shut down and reap its worker.
                try:
                    log, _ = master.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(master.pid, signal.SIGKILL)
                    log, _ = master.communicate()
            self.assertEqual(master.returncode, 0)
            self.assertRegex(log, r'(?m)^served <([0-9]+)> /status 503\nserved <\1> /miningdifficulty 503$')


if __name__ == '__main__':
    unittest.main()
