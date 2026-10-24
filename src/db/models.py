from mongoengine import Document, EmbeddedDocument
from mongoengine.fields import StringField, IntField, ListField, FloatField, BooleanField, DateTimeField, \
    EmbeddedDocumentListField, EmbeddedDocumentField, DecimalField


class RequestCache(Document):
    type = StringField(required=True)
    params = ListField()
    result = ListField()
    last_accessed = IntField(required=True)


class CacheState(Document):
    id = StringField(primary_key=True)
    generation = StringField(required=True)
    ready = BooleanField(default=False)
    writes_complete = BooleanField(default=False)
    verification_height = IntField()
    verification_hash = StringField()

    meta = {'collection': 'cache_state'}


class QueryCacheEntry(Document):
    id = StringField(primary_key=True)
    result = ListField(required=True)
    expires_at = DateTimeField(required=True)

    meta = {
        'collection': 'query_cache_v2',
        'indexes': [{'fields': ['expires_at'], 'expireAfterSeconds': 0}],
    }


class UTXO(Document):
    id = StringField(required=True, primary_key=True)
    address = StringField(required=True)
    value = FloatField(required=True, default=0.0)
    insert_time = FloatField(required=True)
    update_time = FloatField(required=True)


class Address(Document):
    hash = StringField(required=True, primary_key=True)
    balance = DecimalField(required=True, precision=8)
    txs = ListField()
    insert_time = FloatField(required=True)
    update_time = FloatField(required=True)


class ScriptPubKey(EmbeddedDocument):
    addresses = ListField()


class Vout(EmbeddedDocument):
    value = FloatField(required=True)
    n = IntField(required=True)
    scriptPubKey = EmbeddedDocumentField(ScriptPubKey)


class Vin(EmbeddedDocument):
    coinbase = StringField()
    txid = StringField()
    vout = IntField()


class Tx(EmbeddedDocument):
    txid = StringField(required=True, primary_key=True)
    hash = StringField(required=True)
    type = StringField()
    status = StringField()
    vin = EmbeddedDocumentListField(Vin)
    vout = EmbeddedDocumentListField(Vout)


class Block(Document):
    hash = StringField(required=True, primary_key=True)
    previousblockhash = StringField(default='')
    tx = EmbeddedDocumentListField(Tx)
    atx = EmbeddedDocumentListField(Tx)
    time = IntField(required=True)
    height = IntField(required=True)
    nextblockhash = StringField()
    difficulty = FloatField(required=True)
    insert_time = FloatField(required=True)
    update_time = FloatField(required=True)

    meta = {
        'index_background': True,
        'indexes': ['height']
    }
