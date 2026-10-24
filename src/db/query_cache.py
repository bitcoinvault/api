import hashlib
import logging
import uuid
from datetime import datetime, timedelta

from bson import BSON
from mongoengine.errors import NotUniqueError, OperationError
from pymongo.errors import PyMongoError

from db.models import CacheState, QueryCacheEntry


CACHE_TTL_SECONDS = 3600
STATE_ID = 'blockchain'


def assert_no_pending_update():
    state = CacheState.objects(id=STATE_ID).first()
    if state is not None and not state.ready:
        raise RuntimeError('An unfinished blockchain update requires verified recovery')
    return state


def get_pending_verification():
    return CacheState.objects(id=STATE_ID, ready=False, writes_complete=True).first()


def begin_update():
    state = assert_no_pending_update()
    generation = uuid.uuid4().hex
    if state is None:
        try:
            CacheState(id=STATE_ID, generation=generation, ready=False).save(force_insert=True)
        except NotUniqueError as error:
            raise RuntimeError('A concurrent blockchain update has started') from error
    else:
        updated = CacheState.objects(
            id=STATE_ID, generation=state.generation, ready=True).update_one(
                set__generation=generation, set__ready=False, set__writes_complete=False,
                unset__verification_height=1, unset__verification_hash=1)
        if updated != 1:
            raise RuntimeError('A concurrent blockchain update has started')
    return generation


def invalidate():
    CacheState.objects(id=STATE_ID).update_one(
        upsert=True, set__generation=uuid.uuid4().hex, set__ready=False,
        set__writes_complete=False, unset__verification_height=1, unset__verification_hash=1)


def mark_writes_complete(generation, height, block_hash):
    updated = CacheState.objects(
        id=STATE_ID, generation=generation, ready=False).update_one(
            set__writes_complete=True, set__verification_height=height,
            set__verification_hash=block_hash)
    if updated != 1:
        raise RuntimeError('Cache generation changed before verification')


def complete_update(generation):
    updated = CacheState.objects(
        id=STATE_ID, generation=generation, ready=False).update_one(set__ready=True)
    if updated != 1:
        raise RuntimeError('Cache generation changed while updating blockchain data')


def get_generation():
    state = CacheState.objects(id=STATE_ID, ready=True).first()
    return state.generation if state is not None else None


def _generation_or_none():
    try:
        return get_generation()
    except (PyMongoError, OperationError) as error:
        logging.warning('Query cache state unavailable (%s)', type(error).__name__)
        return None


def execute(pipeline, collection, cache_type, params):
    generation = _generation_or_none()
    if generation is None:
        return list(collection.aggregate(pipeline))

    payload = {
        'generation': generation,
        'collection': collection._collection.full_name,
        'type': cache_type,
        'params': params,
        'pipeline': pipeline,
    }
    key = hashlib.sha256(BSON.encode(payload)).hexdigest()
    try:
        entry = QueryCacheEntry.objects(id=key, expires_at__gt=datetime.utcnow()).first()
        if entry is not None:
            return entry.result
    except (PyMongoError, OperationError) as error:
        logging.warning('Query cache read failed (%s)', type(error).__name__)

    result = list(collection.aggregate(pipeline))
    # Never retain a result computed across a daemon update or a reorg.
    if _generation_or_none() == generation:
        try:
            QueryCacheEntry.objects(id=key).update_one(
                upsert=True, set__result=result,
                set__expires_at=datetime.utcnow() + timedelta(seconds=CACHE_TTL_SECONDS))
        except (PyMongoError, OperationError) as error:
            logging.warning('Query cache write failed (%s)', type(error).__name__)
    return result
