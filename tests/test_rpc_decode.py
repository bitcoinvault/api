from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from blockchain import rpc


class CoreDecodeTest(unittest.TestCase):
    def test_decode_uses_configured_core_with_bounded_timeout(self):
        decoded = {'txid': '1' * 64}
        response = Mock(status_code=200)
        response.json.return_value = {'result': decoded, 'error': None}
        with patch.object(rpc.requests, 'post', return_value=response) as post:
            self.assertEqual(rpc.decode_raw_transaction('0100'), decoded)
        args, kwargs = post.call_args
        self.assertEqual(args, (rpc.url,))
        self.assertEqual(kwargs['json']['method'], 'decoderawtransaction')
        self.assertEqual(kwargs['json']['params'], ['0100'])
        self.assertEqual(kwargs['timeout'], (3, 10))
        self.assertFalse(kwargs['allow_redirects'])

    def test_errors_do_not_supply_an_accounting_record(self):
        for status, body in ((302, {'result': {}}), (500, {'result': {}}),
                             (200, {'error': {'code': -22}, 'result': {}}), (200, {'result': None})):
            response = Mock(status_code=status)
            response.json.return_value = body
            with self.subTest(status=status, body=body), patch.object(rpc.requests, 'post', return_value=response):
                with self.assertRaisesRegex(ValueError, 'Core could not decode'):
                    rpc.decode_raw_transaction('0100')
        with patch.object(rpc.requests, 'post', side_effect=rpc.requests.exceptions.Timeout):
            with self.assertRaisesRegex(ValueError, 'Core could not decode'):
                rpc.decode_raw_transaction('0100')
