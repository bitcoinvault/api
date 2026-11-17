"""Exercise the real changed functions without connecting to a database."""
import ast
import json
import logging
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

ROOT = Path('/app') if Path('/app/daemon.py').exists() else Path(__file__).resolve().parents[1] / 'src'


def load_functions(path, names, assignments=()):
    tree = ast.parse(path.read_text())
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            nodes.append(node)
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in assignments for t in node.targets
        ):
            nodes.append(node)
    return compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), 'exec')


class WarmIngestTest(unittest.TestCase):
    def run_ingest(self, flag=None, count=12, fail=False):
        events = []
        analyzer = Mock()
        analyzer.max_block_number.return_value = 10

        def insert(block, new, deleted):
            events.append(('insert', block))
            analyzer.max_block_number.return_value = block
            new[block] = 'utxo'
            if fail:
                raise RuntimeError('injected import failure')

        def warm(name):
            return lambda *args: events.append(('warm', name, args))

        analyzer.piechart_data.side_effect = warm('pie')
        analyzer.mining_difficulty.side_effect = warm('difficulty')
        analyzer.transaction_volume.side_effect = warm('volume')
        ns = {
            'os': os, 'analyzer': analyzer, 'get_block_count': lambda: count,
            'get_block': lambda i: i, 'insert_block': insert,
            'update_analyzer': lambda: events.append(('update_analyzer',)),
            'update_addresses': lambda n, d: events.append(('addresses', dict(n), dict(d))),
            'logging': Mock(),
        }
        code = load_functions(ROOT / 'daemon.py',
                              ['_crop_params', 'period_to_block_range_and_interval', 'update_blockchain'],
                              ['MAX_CHUNK_SIZE', 'period_block_map', 'PREWARM_ALL_STATS'])
        with patch.dict(os.environ, {}, clear=True):
            if flag is not None:
                os.environ['PREWARM_ALL_STATS'] = flag
            exec(code, ns)
        if fail:
            with self.assertRaises(RuntimeError):
                ns['update_blockchain']()
        else:
            ns['update_blockchain']()
        return events, ns

    def test_disable_preserves_ingestion_and_short_period_warming(self):
        enabled, _ = self.run_ingest()
        disabled, _ = self.run_ingest('false')
        self.assertEqual([e for e in enabled if e[0] != 'warm'],
                         [e for e in disabled if e[0] != 'warm'])
        self.assertEqual([e[1] for e in disabled if e[0] == 'insert'], [11, 12])
        self.assertEqual(sum(e[0] == 'warm' for e in enabled), 7)
        self.assertEqual(sum(e[0] == 'warm' for e in disabled), 5)
        address_pos = next(i for i, e in enumerate(disabled) if e[0] == 'addresses')
        self.assertTrue(all(i > address_pos for i, e in enumerate(disabled) if e[0] == 'warm'))

    def test_no_new_blocks_does_not_warm(self):
        events, _ = self.run_ingest('false', count=10)
        self.assertFalse(any(e[0] in ['insert', 'warm'] for e in events))

    def test_failed_import_never_warms(self):
        events, _ = self.run_ingest('false', fail=True)
        self.assertFalse(any(e[0] == 'warm' for e in events))

    def test_false_values_and_default_enabled(self):
        for value in ['false', '0', 'no', ' FALSE ']:
            self.assertFalse(self.run_ingest(value)[1]['PREWARM_ALL_STATS'])
        self.assertTrue(self.run_ingest()[1]['PREWARM_ALL_STATS'])
        self.assertTrue(self.run_ingest('true')[1]['PREWARM_ALL_STATS'])


class CacheReadTest(unittest.TestCase):
    def make(self, count):
        results = Mock()
        results.count.return_value = count
        entry = Mock()
        entry.result = [{'cached': True}]
        results.first.return_value = entry
        cache = Mock()
        cache.filter.return_value = results
        results.filter_mock = cache.filter
        collection = Mock()
        collection._document._meta = {'id_field': 'hash'}
        collection.aggregate.return_value = [{'computed': True}]
        model = Mock()
        ns = {
            'get_cache': lambda: cache, 'get_highest_block_number_in_db': lambda: 42,
            'logging': logging, 'json': json, 'RequestCache': model,
            'OperationFailure': type('OperationFailure', (Exception,), {}),
        }
        exec(load_functions(ROOT / 'db' / 'db.py', ['execute_query']), ns)
        return ns['execute_query'], results, entry, collection, model

    def test_hit_returns_existing_result_with_one_count_and_one_fetch(self):
        f, q, e, c, m = self.make(1)
        self.assertEqual(f([{'$match': {}}], c, type='x', params=[1]), [{'cached': True}])
        q.count.assert_called_once()
        q.first.assert_called_once()
        e.update.assert_called_once_with(last_accessed=42)
        c.aggregate.assert_not_called()
        m.from_json.assert_not_called()

    def test_miss_counts_once_and_keeps_computation_and_cache_write(self):
        f, q, e, c, m = self.make(0)
        self.assertEqual(f([{'$match': {}}], c, type='x', params=[1]), [{'computed': True}])
        q.count.assert_called_once()
        q.first.assert_not_called()
        c.aggregate.assert_called_once()
        m.from_json.return_value.save.assert_called_once()

    def test_duplicate_result_keeps_existing_miss_behavior(self):
        f, q, e, c, m = self.make(2)
        self.assertEqual(f([], c, type='x', params=[]), [{'computed': True}])
        q.count.assert_called_once()
        q.first.assert_not_called()
        c.aggregate.assert_called_once()

    def test_queries_without_cache_metadata_are_unchanged(self):
        f, q, e, c, m = self.make(1)
        self.assertEqual(f([], c), [{'computed': True}])
        q.count.assert_not_called()
        q.first.assert_not_called()
        m.from_json.assert_not_called()

    def test_chart_lookup_uses_upper_height_and_full_array_equality(self):
        for params in ([6, 1, 9000], (144, 4670, 9000)):
            f, q, e, c, m = self.make(1)
            self.assertEqual(f([], c, type='miningdifficulty', params=params), [{'cached': True}])
            q.filter_mock.assert_called_once_with(type='miningdifficulty', __raw__={
                'params': 9000,
                '$expr': {'$eq': ['$params', {'$literal': list(params)}]}
            })

    def test_non_chart_params_keep_the_original_lookup(self):
        for params in ([], [1], [1, 2], [1, 2, 3, 4], [1, '2', 3], [True, 2, 3], None):
            f, q, e, c, m = self.make(1)
            self.assertEqual(f([], c, type='custom', params=params), [{'cached': True}])
            q.filter_mock.assert_called_once_with(type='custom', params=params)

    def test_existence_check_does_not_materialize_the_full_document(self):
        class Collection:
            _document = type('Document', (), {'_meta': {'id_field': 'hash'}})

            def __bool__(self):
                raise AssertionError('unprojected truthiness would load the large block')

            def only(self, field):
                if field != 'hash':
                    raise AssertionError('expected the primary key projection')
                return True

            def aggregate(self, *pipeline):
                return [{'value': 7}]

        f, q, e, c, m = self.make(1)
        self.assertEqual(f([], Collection()), [{'value': 7}])

    def test_empty_collection_preserves_defaults_and_errors(self):
        f, q, e, c, m = self.make(1)
        c.only.return_value = False
        self.assertEqual(f([], c, default=[{'height': -1}]), [{'height': -1}])
        c.aggregate.assert_not_called()
        with self.assertRaisesRegex(Exception, 'empty collection'):
            f([], c)

    def test_none_collection_preserves_defaults_and_errors(self):
        f, q, e, c, m = self.make(1)
        self.assertEqual(f([], None, default=[]), [])
        with self.assertRaisesRegex(Exception, 'empty collection'):
            f([], None)


if __name__ == '__main__':
    unittest.main(verbosity=2)
