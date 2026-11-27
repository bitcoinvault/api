from functools import lru_cache

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from db.db import get_db_uri
from db.models import Block


class ReaderIndexUnavailable(RuntimeError):
    pass


def configure_block_index(mode):
    if mode == 'manage':
        return None
    if mode != 'verify':
        raise ValueError('API_BLOCK_INDEX_MODE must be manage or verify')
    # Set before the first QuerySet is constructed, in the HTTP process only.
    Block._meta['auto_create_index'] = False
    return require_block_index


@lru_cache(maxsize=1)
def require_block_index(worker_pid):
    # A preloaded worker must verify for itself. Cache successes, never failures.
    try:
        # Bound the cold probe without changing the serving client's query timeouts.
        with MongoClient(get_db_uri(), connect=False, retryReads=False,
                         serverSelectionTimeoutMS=1500, connectTimeoutMS=1500,
                         socketTimeoutMS=2500, waitQueueTimeoutMS=1500) as client:
            collection = client.get_default_database()[Block._get_collection_name()]
            return _check_height_index(collection)
    except PyMongoError:
        raise ReaderIndexUnavailable('Cannot verify the block height index') from None


def _check_height_index(collection):
    with collection.list_indexes() as indexes:
        for index in indexes:
            if (list(index['key'].items()) == [('height', 1)]
                    and not index.get('hidden') and not index.get('sparse')
                    and 'partialFilterExpression' not in index and 'buildUUID' not in index
                    and index.get('collation', {}).get('locale', 'simple') == 'simple'):
                # A catalog entry alone need not be queryable yet.
                collection.find_one({}, {'_id': 0, 'height': 1}, hint=index['name'],
                                    sort=[('height', -1)], max_time_ms=2000)
                return
    raise ReaderIndexUnavailable('Required ascending block height index is missing or unusable')
