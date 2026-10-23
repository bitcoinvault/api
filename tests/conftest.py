import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import mongoengine
import pytest


URI = os.environ.get('TEST_MONGODB_URI', '')
PARSED_URI = urlsplit(URI)
HOST = PARSED_URI.hostname
if (PARSED_URI.scheme != 'mongodb' or HOST != 'gw-cache-mongo-20261023' or
        PARSED_URI.port not in (None, 27017) or PARSED_URI.path not in ('', '/') or
        PARSED_URI.username is not None or PARSED_URI.password is not None or
        PARSED_URI.query or PARSED_URI.fragment):
    raise pytest.UsageError('TEST_MONGODB_URI must point only to the isolated local test MongoDB')

DATABASE = 'cache_fix_tests_' + uuid.uuid4().hex
os.environ['DB_PROTOCOL'] = 'mongodb'
os.environ['DB_NAME'] = DATABASE
os.environ['DB_URL'] = HOST
os.environ['DB_PORT'] = str(urlsplit(URI).port or 27017)
os.environ['DB_USERNAME'] = ''
os.environ['DB_PASSWORD'] = ''
os.environ['DB_URI_PARAMS'] = ''
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


@pytest.fixture(autouse=True)
def block_http_requests(monkeypatch):
    import requests

    def blocked(*args, **kwargs):
        raise AssertionError('HTTP and chain RPC are disabled in cache tests')

    monkeypatch.setattr(requests.sessions.Session, 'request', blocked)


@pytest.fixture(scope='session', autouse=True)
def connection():
    mongoengine.disconnect()
    client = mongoengine.connect(DATABASE, host=URI, serverSelectionTimeoutMS=5000)
    assert mongoengine.connection.get_db().name == DATABASE
    client.admin.command('ping')
    try:
        yield client
    finally:
        client.drop_database(DATABASE)
        mongoengine.disconnect()


@pytest.fixture(autouse=True)
def clean_database(connection):
    database = connection[DATABASE]
    for name in database.list_collection_names():
        database[name].delete_many({})
    database.cache_state.insert_one({
        '_id': 'blockchain', 'generation': 'initial', 'ready': True,
    })
    yield database


@pytest.fixture
def chain_data(clean_database):
    from db.models import Address, Block

    Block(hash='block-10', height=10, time=1, difficulty=1,
          insert_time=1, update_time=1).save()
    Address(hash='address-one', balance=7, insert_time=1, update_time=1).save()


@pytest.fixture
def address_aggregations(monkeypatch):
    from db.models import Address
    from mongoengine.queryset import QuerySet

    calls = []
    original = QuerySet.aggregate

    def record(queryset, *args, **kwargs):
        if queryset._document is Address:
            calls.append(args)
        return original(queryset, *args, **kwargs)

    monkeypatch.setattr(QuerySet, 'aggregate', record)
    return calls
