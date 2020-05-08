from datetime import datetime
from models import Address
import json
    
def set_insert_or_update_time(db_object, ins=True, upd=True):
    time = datetime.now().timestamp()
    
    if ins: db_object.insert_time = time
    if upd: db_object.update_time = time
    
    
def create_address(addr, amount):
    json_address = json.dumps({'hash':addr, 'balance':amount, 'txs':[]})
    return Address.from_json(json_address)