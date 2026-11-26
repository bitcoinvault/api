from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
try:
    from db import reader_index as reader
    from pymongo.errors import OperationFailure
except ModuleNotFoundError as error:
    if error.name not in ('bson', 'pymongo', 'mongoengine'):
        raise
    reader = None


@unittest.skipUnless(reader, 'requires API runtime dependencies')
class ReaderIndexTests(unittest.TestCase):
    def setUp(self):
        reader.require_block_index.cache_clear()
        self.addCleanup(reader.require_block_index.cache_clear)
        model_patch = patch.object(reader, 'Block')
        self.model = model_patch.start()
        self.addCleanup(model_patch.stop)
        self.model._meta = {}
        self.collection = self.model._get_collection.return_value
        self.ready = {'name': 'custom_height', 'key': {'height': 1}}

    def catalog(self, *rows):
        cursor = MagicMock()
        cursor.__enter__.return_value = rows
        self.collection.list_indexes.return_value = cursor

    def test_configure_does_not_connect_or_change_the_default(self):
        self.assertIsNone(reader.configure_block_index('manage'))
        with self.assertRaises(ValueError):
            reader.configure_block_index('other')
        self.assertEqual(self.model._meta, {})
        self.assertIs(reader.configure_block_index('verify'), reader.require_block_index)
        self.assertIs(self.model._meta['auto_create_index'], False)
        self.model._get_collection.assert_not_called()

    def test_only_complete_visible_height_index_is_eligible(self):
        for row in ({}, {'key': {'height': -1}}, {'key': {'height': 1, 'hash': 1}},
                    {'sparse': True}, {'hidden': True}, {'partialFilterExpression': {}},
                    {'buildUUID': 'in-progress'}, {'collation': {'locale': 'en'}}):
            self.catalog(*([dict(self.ready, **row)] if row else []))
            with self.subTest(row=row), self.assertRaisesRegex(reader.ReaderIndexUnavailable, 'missing or unusable'):
                reader.require_block_index(100)
        self.collection.find_one.assert_not_called()

    def test_success_is_cached_per_worker_even_on_empty_collection(self):
        self.catalog(self.ready)
        self.collection.find_one.return_value = None
        for pid in (100, 100, 101):
            reader.require_block_index(pid)
        self.assertEqual(self.collection.list_indexes.call_count, 2)
        self.assertEqual(self.collection.find_one.call_count, 2)
        self.collection.find_one.assert_called_with({}, {'_id': 0, 'height': 1},
                                                  hint='custom_height', sort=[('height', -1)], max_time_ms=2000)

    def test_failed_catalog_or_hint_is_not_cached(self):
        self.catalog(self.ready)
        self.collection.list_indexes.side_effect = OperationFailure('private error')
        with self.assertRaisesRegex(reader.ReaderIndexUnavailable, '^Cannot verify the block height index$'):
            reader.require_block_index(100)
        self.collection.list_indexes.side_effect = None
        self.collection.find_one.side_effect = OperationFailure('private error')
        with self.assertRaisesRegex(reader.ReaderIndexUnavailable, '^Cannot verify the block height index$'):
            reader.require_block_index(100)
        self.collection.find_one.side_effect = None
        reader.require_block_index(100)
        self.assertEqual(self.collection.list_indexes.call_count, 3)
