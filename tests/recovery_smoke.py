"""Exercise BTCV update-worker recovery, not the full service lifecycle."""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

import requests


CACHE_COLLECTIONS = frozenset(('cache_state', 'query_cache_v2', 'request_cache'))
PROXY_VARIABLES = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                   'http_proxy', 'https_proxy', 'all_proxy')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def validate_environment(values):
    match = re.fullmatch(
        r'(api_cache_http_[A-Za-z0-9_]+)_recovery_(source|restored)',
        values.get('DB_NAME', ''))
    allowed = (
        match is not None and len(match.group(1) + '_recovery_restored') <= 63 and
        values.get('DB_URL') == 'api-cache-test-mongo' and
        values.get('DB_PROTOCOL', 'mongodb') == 'mongodb' and
        values.get('DB_PORT', '27017') == '27017' and
        values.get('RPC_HOST') == 'api-cache-test-rpc' and
        values.get('RPC_PORT', '8332') == '8332' and
        not any(values.get(key) for key in (
            'DB_USERNAME', 'DB_PASSWORD', 'DB_URI_PARAMS',
            'RPC_USER', 'RPC_PASSWORD') + PROXY_VARIABLES))
    if not allowed:
        raise RuntimeError('Recovery drill requires isolated local services and fixture databases')
    return tuple(match.group(1) + '_recovery_' + role for role in ('source', 'restored'))


def application_digest(database):
    from bson import BSON, json_util

    digest = hashlib.sha256()
    for name in sorted(database.list_collection_names()):
        if name in CACHE_COLLECTIONS:
            continue
        collection = database[name]
        indexes = {}
        for index_name, options in collection.index_information().items():
            indexes[index_name] = {key: value for key, value in options.items()
                                   if key not in ('ns', 'v')}
        metadata = json_util.dumps(
            {'options': collection.options(), 'indexes': indexes},
            json_options=json_util.CANONICAL_JSON_OPTIONS,
            sort_keys=True, separators=(',', ':'))
        digest.update(BSON.encode({'collection': name, 'metadata': metadata}))
        for document in collection.find().sort('_id', 1):
            digest.update(BSON.encode(document))
    return digest.hexdigest()


def verify_restored_copy(source, restored):
    if CACHE_COLLECTIONS.intersection(restored.list_collection_names()):
        raise RuntimeError('Restored database must not contain cache collections')
    if application_digest(source) != application_digest(restored):
        raise RuntimeError('Restored application BSON, collection options or indexes differ')


def verify_stopped_state(database, expected_digest):
    require(application_digest(database) == expected_digest,
            'Stopped worker changed application data')
    state = database.cache_state.find_one({'_id': 'blockchain'})
    require(state is not None and state.get('ready') is False and
            state.get('writes_complete', False) is False,
            'Stopped worker changed cache readiness')


def run_once(database, mode='normal', expected_error=None):
    env = dict(os.environ, DB_NAME=database)
    result = subprocess.run(
        [sys.executable, __file__, '--child', mode], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    if expected_error is None:
        if result.returncode != 0:
            raise RuntimeError('Recovery worker failed: ' + result.stdout + result.stderr[-1500:])
    elif result.returncode != 2 or expected_error not in result.stdout:
        raise RuntimeError('Recovery worker did not reject ' + mode + ': ' + result.stdout)


def kill_at_checkpoint(database, mode):
    with tempfile.TemporaryDirectory(prefix='api-recovery-') as directory:
        checkpoint = Path(directory) / 'checkpoint'
        log_path = Path(directory) / 'worker.log'
        with log_path.open('w') as output:
            process = subprocess.Popen(
                [sys.executable, __file__, '--child', mode, '--checkpoint', str(checkpoint)],
                env=dict(os.environ, DB_NAME=database),
                stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                while not checkpoint.exists():
                    if process.poll() is not None:
                        log = log_path.read_text(errors='replace')[-1500:]
                        raise RuntimeError('Worker exited before checkpoint: ' + log)
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Recovery checkpoint timed out')
                    time.sleep(0.05)
                process.kill()
                process.wait(timeout=10)
                require(process.returncode == -9,
                        'Recovery drill did not terminate the worker with SIGKILL')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)


def child(mode, checkpoint):
    from mongoengine import connect, disconnect
    from db.db import get_db_uri
    from db.models import Address
    from db import query_cache
    import daemon

    def pause():
        Path(checkpoint).write_text('paused', encoding='ascii')
        while True:
            time.sleep(1)

    connect(host=get_db_uri(), serverSelectionTimeoutMS=5000)
    try:
        if mode == 'baseline':
            daemon.get_block_count = lambda: 0
        elif mode == 'partial':
            original = daemon.insert_block

            def insert_then_pause(*args, **kwargs):
                original(*args, **kwargs)
                pause()

            daemon.insert_block = insert_then_pause
        elif mode == 'complete':
            daemon._verify_completed_update = lambda *args: pause()
        elif mode == 'reorg':
            daemon.get_block_hash = lambda height: 'different-fixture-fork'
        daemon.update_analyzer()
        daemon.update_blockchain()
        if mode == 'baseline':
            query_cache.execute([{'$project': {'balance': 1}}], Address.objects,
                                'recovery-baseline', [])
    finally:
        disconnect()


def verify_fixture(database, height):
    from db.models import Address, Block, Tx, UTXO

    blocks = database[Block._get_collection_name()]
    addresses = database[Address._get_collection_name()]
    utxos = database[UTXO._get_collection_name()]
    require(height in (0, 1), 'Unexpected fixture height')
    require(blocks.count_documents({}) == height + 1, 'Unexpected fixture block count')
    tip = blocks.find_one({'height': height})
    require(tip is not None and tip['_id'] == 'block-' + str(height),
            'Unexpected fixture canonical tip')
    for block_height in range(height + 1):
        stored = blocks.find_one({'height': block_height})
        require(stored is not None and stored['_id'] == 'block-' + str(block_height),
                'Unexpected fixture block hash')
        require(stored.get('previousblockhash', '') == ('' if block_height == 0 else 'block-0'),
                'Unexpected fixture block continuity')
        alerts = stored.get('atx')
        require(isinstance(alerts, list) and len(alerts) == 1,
                'BTCV alert metadata missing')
        alert = alerts[0]
        require(alert.get(Tx._fields['txid'].db_field) == 'alert-' + str(block_height) and
                alert.get('hash') == 'alert-' + str(block_height) and
                alert.get('type') == 'alert' and alert.get('status') == 'pending' and
                alert.get('vin') == [{'txid': 'origin' if block_height == 0 else 'spend', 'vout': 0}] and
                alert.get('vout') == [{'value': 97.0, 'n': 0,
                                      'scriptPubKey': {'addresses': ['alert-recipient']}}],
                'Unexpected BTCV alert metadata')
    require(addresses.count_documents({}) == height + 1,
            'Unexpected fixture address count or alert balance')
    sender = addresses.find_one({'_id': 'sender'})
    require(sender is not None and Decimal(str(sender['balance'])) == (2 if height == 0 else 0),
            'Unexpected fixture sender balance')
    require(utxos.count_documents({}) == 1, 'Unexpected fixture UTXO count')
    expected_utxo = 'origin0' if height == 0 else 'spend0'
    utxo = utxos.find_one({'_id': expected_utxo})
    require(utxo is not None and utxo['value'] == (2 if height == 0 else 3),
            'Unexpected fixture UTXO value')
    require(utxo.get('address') == ('sender' if height == 0 else 'recipient'),
            'Unexpected fixture UTXO owner')
    if height == 1:
        recipient = addresses.find_one({'_id': 'recipient'})
        require(recipient is not None and Decimal(str(recipient['balance'])) == 3,
                'Unexpected fixture recipient balance')


def cache_digest(database):
    from bson import BSON

    names = CACHE_COLLECTIONS.intersection(database.list_collection_names())
    return hashlib.sha256(BSON.encode({
        name: list(database[name].find().sort('_id', 1)) for name in sorted(names)
    })).hexdigest()


def stop_owned_process(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def read_api(session, base_url, route):
    require(re.fullmatch(r'http://127\.0\.0\.1:[0-9]{1,5}', base_url) is not None and
            1024 <= int(base_url.rsplit(':', 1)[1]) <= 65535 and
            route in ('/status', '/nonemptywalletsnumber'),
            'Recovery API requests require an allowed loopback fixture URL')
    response = session.get(base_url + route, timeout=5, allow_redirects=False)
    require(response.status_code == 200, 'Recovery API returned a non-200 response')
    return response.json()


def verify_api(get_json):
    require(get_json('/status') == {'height': 1, 'hashrate': 123, 'total_coin_supply': 3.0},
            'Restored API status differs from the independently known fixture')
    require(get_json('/nonemptywalletsnumber') == 1,
            'Restored API wallet count differs from the independently known fixture')


@contextmanager
def restored_api(database):
    env = dict(os.environ, DB_NAME=database)
    validate_environment(env)
    require(database.endswith('_recovery_restored'), 'API requires the restored fixture database')
    # Pass our own bound listener to Gunicorn: no port race or preexisting server.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(16)
        base_url = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        with tempfile.TemporaryFile() as output, requests.Session() as session:
            session.trust_env = False
            process = subprocess.Popen(
                [sys.executable, '-m', 'gunicorn', '-w', '1', '--bind',
                 'fd://' + str(listener.fileno()), '--access-logfile', '-', 'api:app'],
                cwd='/app', env=env, pass_fds=(listener.fileno(),),
                stdout=output, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 30
                while True:
                    require(process.poll() is None, 'Recovery API exited before readiness')
                    try:
                        status = read_api(session, base_url, '/status')
                        require(status == {'height': 1, 'hashrate': 123, 'total_coin_supply': 3.0},
                                'Recovery API started with unexpected fixture data')
                        break
                    except (requests.ConnectionError, requests.Timeout):
                        require(time.monotonic() < deadline, 'Recovery API startup timed out')
                        time.sleep(0.05)
                yield lambda route: read_api(session, base_url, route)
            finally:
                stop_owned_process(process)


def exercise(source, restored):
    verify_fixture(source, 0)
    verify_restored_copy(source, restored)
    verify_fixture(restored, 0)
    baseline = application_digest(source)

    kill_at_checkpoint(source.name, 'partial')
    state = source.cache_state.find_one({'_id': 'blockchain'})
    require(state is not None and state['ready'] is False and
            state.get('writes_complete', False) is False, 'Partial writes were not marked unready')
    interrupted = application_digest(source)
    require(interrupted != baseline, 'Interrupted worker did not modify application data')
    run_once(source.name, expected_error='unfinished')
    verify_stopped_state(source, interrupted)

    # The restored database was checked before either interrupted write and is
    # separate from the damaged source; never reset the source's readiness flag.
    require(application_digest(restored) == baseline, 'Restored application baseline changed')
    kill_at_checkpoint(restored.name, 'complete')
    verify_fixture(restored, 1)
    state = restored.cache_state.find_one({'_id': 'blockchain'})
    require(state is not None and state['ready'] is False and state['writes_complete'] is True,
            'Completed writes were not marked for verification')
    require(state.get('verification_height') == 1 and state.get('verification_hash') == 'block-1',
            'Completed writes do not name the independently known fixture tip')
    generation = state['generation']
    completed = application_digest(restored)
    run_once(restored.name)
    require(application_digest(restored) == completed, 'Restart replayed completed writes')
    state = restored.cache_state.find_one({'_id': 'blockchain'})
    require(state is not None and state['ready'] is True and state['generation'] == generation,
            'Restart did not verify the same completed generation')
    verify_fixture(restored, 1)

    with restored_api(restored.name) as get_json:
        verify_api(get_json)
        verify_fixture(restored, 1)
        require(application_digest(restored) == completed, 'API changed restored application data')
        run_once(restored.name, mode='reorg', expected_error='canonical')
        verify_stopped_state(restored, completed)
        disabled_cache = cache_digest(restored)
        for _ in range(2):
            verify_api(get_json)
        verify_stopped_state(restored, completed)
        require(cache_digest(restored) == disabled_cache,
                'API changed cache or certified readiness after the fork')
        run_once(restored.name, expected_error='unfinished')
        verify_stopped_state(restored, completed)
    return {
        'result': 'passed', 'scope': 'synthetic_btcv_update_worker',
        'full_service_lifecycle': 'not_exercised', 'baseline_application_sha256': baseline,
        'backup_bson_options_and_indexes': 'matched', 'cache_metadata_restored': False,
        'btcv_alert_metadata': 'preserved_without_balances_or_utxos',
        'partial_write_restart': 'blocked_without_replay',
        'completed_write_restart': 'verified_without_double_balances',
        'reorg_restart': 'blocked_without_data_mutation',
        'restored_gunicorn_api': 'known_fixture_status_and_wallet_count',
        'api_after_fork': 'reads_without_cache_writes_or_readiness_certification',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', nargs='?', choices=('prepare', 'exercise'))
    parser.add_argument('--child', choices=('normal', 'baseline', 'partial', 'complete', 'reorg'))
    parser.add_argument('--checkpoint')
    args = parser.parse_args()
    source_name, restored_name = validate_environment(os.environ)
    # Empty overrides mask any image credentials; use rpc.py's fixture defaults.
    for key in ('RPC_USER', 'RPC_PASSWORD'):
        os.environ.pop(key, None)
    sys.path.insert(0, '/app')
    if args.child:
        if args.child in ('partial', 'complete') and not args.checkpoint:
            raise RuntimeError('An interruption checkpoint is required')
        try:
            child(args.child, args.checkpoint)
        except RuntimeError as error:
            print(json.dumps({'error': str(error)}))
            return 2
        return 0
    if args.phase is None:
        parser.error('prepare or exercise is required')
    from pymongo import MongoClient

    client = MongoClient('mongodb://api-cache-test-mongo:27017', serverSelectionTimeoutMS=5000)
    try:
        source, restored = client[source_name], client[restored_name]
        if args.phase == 'prepare':
            if source.list_collection_names() or restored.list_collection_names():
                raise RuntimeError('Recovery fixture databases must be empty')
            run_once(source_name, 'baseline')
            verify_fixture(source, 0)
            source.command('collMod', 'block', validator={'height': {'$type': 'int'}},
                           validationLevel='strict')
            source.block.create_index([('height', 1), ('time', -1)],
                                      name='recovery_fixture_height_time')
            require(source.query_cache_v2.count_documents({}) > 0, 'Baseline cache was not populated')
            source.request_cache.insert_one({'_id': 'legacy-fixture', 'result': []})
            result = {'result': 'prepared', 'application_sha256': application_digest(source)}
        else:
            result = exercise(source, restored)
        print(json.dumps(result, sort_keys=True))
    finally:
        client.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
