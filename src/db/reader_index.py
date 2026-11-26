from functools import lru_cache

from pymongo.errors import PyMongoError

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
        collection = Block._get_collection()
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
    except PyMongoError:
        raise ReaderIndexUnavailable('Cannot verify the block height index') from None
    raise ReaderIndexUnavailable('Required ascending block height index is missing or unusable')
