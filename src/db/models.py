from mongoengine import Document, EmbeddedDocument
from mongoengine.fields import StringField, IntField, ListField, FloatField,\
    EmbeddedDocumentListField, EmbeddedDocumentField, DecimalField
    
class RequestCache(Document):
    type = StringField(required=True)
    params = ListField()
    result = ListField()
    last_accessed = IntField(required=True)
    
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
        'index_background' : True,
        'indexes' : ['height']
    }   
