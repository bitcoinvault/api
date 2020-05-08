from mongoengine import Document, EmbeddedDocument
from mongoengine.fields import StringField, IntField, ListField, FloatField,\
    EmbeddedDocumentListField, EmbeddedDocumentField
    
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
    balance = FloatField(required=True)
    txs = ListField(required=True)
    insert_time = FloatField(required=True)
    update_time = FloatField(required=True)
    
class ScriptPubKey(EmbeddedDocument):
    asm = StringField(required=True)
    hex = StringField(required=True, primary_key=True)
    reqSigs = IntField()
    type = StringField(required=True)
    addresses = ListField()
    
class Vout(EmbeddedDocument):
    value = FloatField(required=True)
    n = IntField(required=True)
    scriptPubKey = EmbeddedDocumentField(ScriptPubKey)

class ScriptSig(EmbeddedDocument):
    asm = StringField(required=True)
    hex = StringField(required=True, primary_key=True)

class Vin(EmbeddedDocument):
    coinbase = StringField()
    txid = StringField()
    vout = IntField()
    scriptSig = EmbeddedDocumentField(ScriptSig)
    txinwitness = ListField()
    sequence = IntField(required=True)

class Tx(EmbeddedDocument):
    txid = StringField(required=True, primary_key=True)
    hash = StringField(required=True)
    version = IntField(required=True)
    size = IntField(required=True)
    vsize = IntField(required=True)
    weight = IntField(required=True)
    locktime = IntField(required=True)
    vin = EmbeddedDocumentListField(Vin)
    vout = EmbeddedDocumentListField(Vout)
    hex = StringField(required=True)
                       

class Block(Document):
    hash = StringField(required=True, primary_key=True)
    confirmations = IntField(required=True)
    nonce = IntField(required=True)
    size = IntField(required=True)
    bits = StringField(required=True)
    strippedsize = IntField(required=True)
    versionHex = StringField(required=True)
    previousblockhash = StringField(default='')
    tx = EmbeddedDocumentListField(Tx)
    chainwork = StringField(required=True)
    merkleroot = StringField(required=True)
    mediantime = IntField(required=True)
    time = IntField(required=True)
    height = IntField(required=True)
    version = IntField(required=True)
    weight = IntField(required=True)
    nextblockhash = StringField()
    nTx = IntField(required=True)
    difficulty = FloatField(required=True)
    insert_time = FloatField(required=True)
    update_time = FloatField(required=True)
    
    meta = {
        'index_background' : True,
        'indexes' : ['height']
    }   