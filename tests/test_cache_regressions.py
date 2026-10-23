from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from mongoengine.queryset import QuerySet

from blockchain import blockchain_analyzer
from db import db, db_queries
from db.models import Address, RequestCache


def query(pipeline=None, **kwargs):
    return db.execute_query(
        pipeline or db_queries.get_total_coin_supply_query(),
        db.get_addresses(), type='sum', params=(1, 10), **kwargs)


def test_legacy_duplicates_do_not_trigger_repeated_computation(
        chain_data, address_aggregations):
    for _ in range(2):
        RequestCache(type='sum', params=[1, 10],
                     result=[{'total_coin_supply': 999}], last_accessed=10).save()

    assert query() == [{'total_coin_supply': 7.0}]
    assert query() == [{'total_coin_supply': 7.0}]
    assert len(address_aggregations) == 1
    assert RequestCache.objects.count() == 2


def test_empty_result_is_a_cache_hit(chain_data, address_aggregations):
    pipeline = [{'$match': {'hash': 'missing'}}]
    assert query(pipeline) == []
    assert query(pipeline) == []
    assert len(address_aggregations) == 1


def test_concurrent_misses_store_one_entry(chain_data, monkeypatch, clean_database):
    barrier = Barrier(8)
    original = QuerySet.aggregate

    def synchronized(queryset, *args, **kwargs):
        if queryset._document is Address:
            barrier.wait(timeout=15)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'aggregate', synchronized)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: query(), range(8)))

    assert results == [[{'total_coin_supply': 7.0}]] * 8
    assert RequestCache.objects.count() <= 1
    assert clean_database.query_cache_v2.count_documents({}) == 1


def test_status_reuses_supply_aggregation(chain_data, address_aggregations, monkeypatch):
    monkeypatch.setattr(blockchain_analyzer, 'get_hashrate', lambda: 123)
    analyzer = blockchain_analyzer.BlockchainAnalyzer(blockchain=db.get_blockchain())

    for _ in range(2):
        assert analyzer.status() == {
            'height': 10, 'hashrate': 123, 'total_coin_supply': 7.0,
        }
    assert len(address_aggregations) == 1


def test_nonempty_address_count_reuses_aggregation(chain_data, address_aggregations):
    analyzer = blockchain_analyzer.BlockchainAnalyzer()
    assert analyzer.non_empty_wallets_number() == 1
    assert analyzer.non_empty_wallets_number() == 1
    assert len(address_aggregations) == 1


def test_queries_without_cache_arguments_remain_uncached(chain_data, address_aggregations):
    for _ in range(2):
        assert db.execute_query(db_queries.get_total_coin_supply_query(),
                                db.get_addresses()) == [{'total_coin_supply': 7.0}]
    assert len(address_aggregations) == 2


def test_empty_collection_preserves_default():
    assert query(default=['Not enough data']) == ['Not enough data']


def test_missing_collection_preserves_default():
    assert db.execute_query([], None, default=[]) == []
    with pytest.raises(Exception, match='empty collection'):
        db.execute_query([], None)
