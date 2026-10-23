"""Exercise a locally built, unmodified gunicorn image against synthetic data."""
import json
import os
import time
from urllib.parse import urlsplit

import requests
from pymongo import MongoClient


def main():
    uri = os.environ['TEST_MONGODB_URI']
    api = os.environ['TEST_API_URL']
    name = os.environ['TEST_DATABASE']
    if (urlsplit(uri).hostname != 'gw-cache-mongo-20261023' or
            not urlsplit(api).hostname.startswith('gw-api-smoke-') or
            not name.startswith('cache_fix_http_')):
        raise RuntimeError('Smoke tests require disposable local services')

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    database = client[name]
    if database.list_collection_names():
        raise RuntimeError('Smoke test database must be empty')
    try:
        database.block.insert_one({'_id': 'block-10', 'height': 10, 'time': 1,
                                   'difficulty': 1, 'insert_time': 1, 'update_time': 1})
        database.address.insert_one({'_id': 'address-one', 'balance': 7,
                                     'txs': [], 'insert_time': 1, 'update_time': 1})
        database.cache_state.insert_one({'_id': 'blockchain', 'generation': 'smoke',
                                         'ready': True})
        database.command('profile', 2)
        for attempt in range(30):
            try:
                response = requests.get(api + '/not-a-route', timeout=2)
                if response.status_code == 404:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        else:
            raise RuntimeError('API did not start')

        for _ in range(10):
            response = requests.get(api + '/status', timeout=5)
            assert response.status_code == 200, response.status_code
            assert response.json() == {'height': 10, 'hashrate': 123, 'total_coin_supply': 7.0}
            response = requests.get(api + '/nonemptywalletsnumber', timeout=5)
            assert response.status_code == 200, response.status_code
            assert response.json() == 1

        aggregates = database.system.profile.count_documents({'command.aggregate': 'address'})
        assert aggregates == 2, aggregates
        database.address.update_one({'_id': 'address-one'}, {'$set': {'balance': 9}})
        database.cache_state.update_one({'_id': 'blockchain'}, {'$set': {'generation': 'next'}})
        assert requests.get(api + '/status', timeout=5).json()['total_coin_supply'] == 9.0
        assert database.system.profile.count_documents({'command.aggregate': 'address'}) == 3
        print(json.dumps({'http_requests': 21, 'initial_address_aggregations': aggregates,
                          'generation_change_observed': True, 'result': 'passed'}))
    finally:
        database.command('profile', 0)
        client.drop_database(name)
        client.close()


if __name__ == '__main__':
    main()
