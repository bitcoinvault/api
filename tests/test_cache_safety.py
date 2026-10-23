from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from mongoengine.queryset import QuerySet
from pymongo.errors import OperationFailure
from pymongo.collection import Collection

from db import db, db_queries, query_cache
from db.models import Address, CacheState, QueryCacheEntry


def query(params=(), pipeline=None):
    return db.execute_query(pipeline or db_queries.get_total_coin_supply_query(),
                            db.get_addresses(), type='sum', params=params)


def test_generation_change_invalidates_existing_result(chain_data, address_aggregations):
    assert query() == [{'total_coin_supply': 7.0}]
    token = query_cache.begin_update()
    Address.objects.update(set__balance=9)
    query_cache.complete_update(token)
    assert query() == [{'total_coin_supply': 9.0}]
    assert len(address_aggregations) == 2


def test_missing_generation_bypasses_cache(chain_data, clean_database, address_aggregations):
    clean_database.cache_state.delete_many({})
    query()
    query()
    assert len(address_aggregations) == 2
    assert QueryCacheEntry.objects.count() == 0


def test_updating_generation_bypasses_old_cache(chain_data, address_aggregations):
    query()
    query_cache.begin_update()
    Address.objects.update(set__balance=9)
    assert query() == [{'total_coin_supply': 9.0}]
    assert query() == [{'total_coin_supply': 9.0}]
    assert len(address_aggregations) == 3
    assert QueryCacheEntry.objects.count() == 1


def test_update_during_aggregation_does_not_store_result(chain_data, monkeypatch):
    original = QuerySet.aggregate

    def change_generation(queryset, *args, **kwargs):
        result = list(original(queryset, *args, **kwargs))
        if queryset._document is Address:
            token = query_cache.begin_update()
            query_cache.complete_update(token)
        return iter(result)

    monkeypatch.setattr(QuerySet, 'aggregate', change_generation)
    assert query() == [{'total_coin_supply': 7.0}]
    assert QueryCacheEntry.objects.count() == 0


def test_expired_entry_is_recomputed(chain_data, address_aggregations):
    query()
    QueryCacheEntry.objects.update(set__expires_at=datetime.utcnow() - timedelta(seconds=1))
    Address.objects.update(set__balance=9)
    assert query() == [{'total_coin_supply': 9.0}]
    assert QueryCacheEntry.objects.count() == 1
    assert len(address_aggregations) == 2


def test_cache_has_ttl_and_unique_scalar_id(chain_data):
    query()
    indexes = QueryCacheEntry._get_collection().index_information()
    assert indexes['_id_']['key'] == [('_id', 1)]
    assert any(index.get('expireAfterSeconds') == 0 and
               index['key'] == [('expires_at', 1)] for index in indexes.values())


def test_array_order_and_pipeline_are_part_of_key(chain_data):
    query(params=(1, 2))
    query(params=(2, 1))
    query(params=(1, 2), pipeline=[{'$match': {'hash': 'missing'}}])
    assert QueryCacheEntry.objects.count() == 3


@pytest.mark.parametrize('operation', ['first', 'update_one'])
def test_cache_failure_returns_computed_result(chain_data, monkeypatch, operation):
    original = getattr(QuerySet, operation)

    def failure(queryset, *args, **kwargs):
        if queryset._document is QueryCacheEntry:
            raise OperationFailure('simulated cache failure')
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, operation, failure)
    assert query() == [{'total_coin_supply': 7.0}]


def test_state_read_failure_bypasses_cache(chain_data, monkeypatch, address_aggregations):
    original = QuerySet.first

    def failure(queryset, *args, **kwargs):
        if queryset._document is CacheState:
            raise OperationFailure('simulated state failure')
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'first', failure)
    query()
    query()
    assert len(address_aggregations) == 2


def test_interrupted_update_cannot_be_silently_republished(chain_data):
    query_cache.begin_update()
    with pytest.raises(RuntimeError, match='unfinished'):
        query_cache.begin_update()
    assert query_cache.get_generation() is None


def test_old_writer_cannot_publish_after_invalidation(chain_data):
    token = query_cache.begin_update()
    query_cache.invalidate()
    with pytest.raises(RuntimeError, match='generation changed'):
        query_cache.complete_update(token)
    assert query_cache.get_generation() is None


def test_first_update_initializes_missing_state(clean_database):
    clean_database.cache_state.delete_many({})
    token = query_cache.begin_update()
    assert query_cache.get_generation() is None
    query_cache.complete_update(token)
    assert query_cache.get_generation() == token


@pytest.mark.parametrize('empty_state', [False, True])
def test_only_one_writer_can_begin_update(clean_database, monkeypatch, empty_state):
    if empty_state:
        clean_database.cache_state.delete_many({})
    barrier = Barrier(2)
    original = QuerySet.first

    def synchronized(queryset, *args, **kwargs):
        result = original(queryset, *args, **kwargs)
        if queryset._document is CacheState:
            barrier.wait(timeout=15)
        return result

    def begin():
        try:
            return query_cache.begin_update()
        except RuntimeError:
            return None

    with monkeypatch.context() as patch:
        patch.setattr(QuerySet, 'first', synchronized)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: begin(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert query_cache.get_generation() is None


def test_cache_lookup_uses_id_index(chain_data):
    query()
    entry = QueryCacheEntry.objects.first()
    plan = QueryCacheEntry._get_collection().find({
        '_id': entry.id, 'expires_at': {'$gt': datetime.utcnow()},
    }).explain()
    assert plan['executionStats']['totalDocsExamined'] <= 1
    assert plan['executionStats']['totalKeysExamined'] <= 1


def test_query_never_reads_legacy_cache(chain_data, monkeypatch):
    def legacy_access():
        raise AssertionError('Legacy cache must not be read')

    monkeypatch.setattr(db, 'get_cache', legacy_access)
    assert query() == [{'total_coin_supply': 7.0}]


def test_compound_sort_order_is_part_of_cache_key(chain_data):
    Address.objects.delete()
    Address(hash='a', balance=9, insert_time=1, update_time=1).save()
    Address(hash='z', balance=1, insert_time=1, update_time=1).save()
    by_balance = [{'$sort': {'balance': 1, '_id': 1}}, {'$limit': 1}]
    by_address = [{'$sort': {'_id': 1, 'balance': 1}}, {'$limit': 1}]
    assert query(pipeline=by_balance)[0]['_id'] == 'z'
    assert query(pipeline=by_address)[0]['_id'] == 'a'


def test_translated_cache_write_error_preserves_result(chain_data, monkeypatch):
    original = Collection.update_one

    def denied(collection, *args, **kwargs):
        if collection.name == 'query_cache_v2':
            raise OperationFailure('simulated write permission failure')
        return original(collection, *args, **kwargs)

    monkeypatch.setattr(Collection, 'update_one', denied)
    assert query() == [{'total_coin_supply': 7.0}]
