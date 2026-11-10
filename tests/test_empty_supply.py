"""Exercise real source methods offline, without MongoDB or driver imports."""
import ast
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


ROOT = Path('/app') if Path('/app/blockchain/blockchain_analyzer.py').exists() else Path(__file__).resolve().parents[1] / 'src'

# Exception doubles follow the dependency-free pattern in test_io_relief.py.
OperationFailure = type('OperationFailure', (Exception,), {})
ConnectionFailure = type('ConnectionFailure', (Exception,), {})


def load_definitions(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [node for node in tree.body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    if {node.name for node in nodes} != set(names):
        raise AssertionError('Missing source definitions: ' + str(names))
    code = compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec')
    exec(code, namespace)


class EmptySupplyTest(unittest.TestCase):
    def setUp(self):
        self.addresses = Mock()
        self.addresses._document._meta = {'id_field': 'hash'}
        self.addresses.only.return_value = True
        self.addresses.aggregate.return_value = iter([])

        # Keep the real helper so a default=[] "fix" would swallow OperationFailure
        # and fail the propagation tests instead of being hidden by a mock.
        db_namespace = {'OperationFailure': OperationFailure}
        load_definitions(ROOT / 'db' / 'db.py', ['execute_query'], db_namespace)
        query_namespace = {}
        load_definitions(ROOT / 'db' / 'db_queries.py',
                         ['get_total_coin_supply_query', 'get_highest_block_number_in_db_query'], query_namespace)
        self.pipeline = query_namespace['get_total_coin_supply_query']()
        namespace = {
            'Decimal': Decimal,
            'db': SimpleNamespace(get_addresses=Mock(return_value=self.addresses),
                                  execute_query=db_namespace['execute_query']),
            'db_queries': SimpleNamespace(**query_namespace),
        }
        load_definitions(ROOT / 'blockchain' / 'blockchain_analyzer.py', ['BlockchainAnalyzer'], namespace)
        self.analyzer = namespace['BlockchainAnalyzer'](addresses=self.addresses)

    def test_empty_addresses_return_zero(self):
        self.addresses.only.return_value = False
        self.assertEqual(self.analyzer.total_coin_supply(), 0)
        self.addresses.aggregate.assert_called_once_with(*self.pipeline)

    def test_unconfigured_blockchain_returns_default_height(self):
        self.assertEqual(self.analyzer.max_block_number(), -1)

    def test_nonempty_supply_is_preserved(self):
        total = Decimal('300.125')
        self.addresses.aggregate.return_value = iter([{'total_coin_supply': total}])
        self.assertIs(self.analyzer.total_coin_supply(), total)
        self.addresses.aggregate.assert_called_once_with(*self.pipeline)

    def test_successful_zero_sum_is_preserved(self):
        total = Decimal('0')
        self.addresses.aggregate.return_value = iter([{'total_coin_supply': total}])
        self.assertIs(self.analyzer.total_coin_supply(), total)

    def assert_failure_propagates(self, error, during_iteration=False):
        if during_iteration:
            def failing_cursor():
                yield {'total_coin_supply': 300.0}
                raise error
            self.addresses.aggregate.return_value = failing_cursor()
        else:
            self.addresses.aggregate.side_effect = error
        with self.assertRaises(type(error)) as raised:
            self.analyzer.total_coin_supply()
        self.assertIs(raised.exception, error)
        self.addresses.aggregate.assert_called_once_with(*self.pipeline)

    def test_operation_failure_propagates(self):
        self.assert_failure_propagates(OperationFailure('aggregation failed'))

    def test_connection_failure_propagates(self):
        self.assert_failure_propagates(ConnectionFailure('connection unavailable'))

    def test_cursor_operation_failure_propagates(self):
        self.assert_failure_propagates(OperationFailure('cursor failed'), during_iteration=True)

    def test_cursor_connection_failure_propagates(self):
        self.assert_failure_propagates(ConnectionFailure('cursor disconnected'), during_iteration=True)

    def test_richest_wallets_empty_returns_empty_list(self):
        self.addresses.only.return_value = False
        self.addresses.order_by.return_value.limit.return_value = []
        self.assertEqual(self.analyzer.richest_wallets(), [])
        self.addresses.order_by.assert_called_once_with('-balance')
        self.addresses.order_by.return_value.limit.assert_called_once_with(20)
        self.addresses.aggregate.assert_called_once_with(*self.pipeline)


if __name__ == '__main__':
    unittest.main(verbosity=2)
