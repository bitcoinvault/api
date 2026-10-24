from copy import deepcopy
import builtins
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import MagicMock, Mock

import pytest

from recovery_smoke import application_digest, validate_environment, verify_restored_copy
import recovery_smoke


def environment():
    return {
        'DB_URL': 'api-cache-test-mongo',
        'DB_NAME': 'api_cache_http_drill_recovery_source',
        'RPC_HOST': 'api-cache-test-rpc',
    }


@pytest.mark.parametrize('key,value', [
    ('DB_URL', 'production.example'),
    ('DB_URL', 'api-cache-test-mongo.example'),
    ('DB_PROTOCOL', 'mongodb+srv'),
    ('DB_PORT', '27018'),
    ('DB_USERNAME', 'real-user'),
    ('DB_PASSWORD', 'not-a-fixture'),
    ('DB_URI_PARAMS', '?authSource=admin'),
    ('DB_NAME', 'blockchain'),
    ('DB_NAME', 'api_cache_http_drill_recovery_source/other'),
    ('DB_NAME', 'api_cache_http_drill'),
    ('DB_NAME', 'api_cache_http_' + 'x' * 33 + '_recovery_source'),
    ('RPC_HOST', 'real-node.example'),
    ('RPC_PORT', '8333'),
    ('RPC_USER', 'user'),
    ('RPC_PASSWORD', 'password'),
    ('RPC_USER', 'fixture@outside.invalid:8332/path?#'),
    ('RPC_PASSWORD', 'fixture@outside.invalid:8332/path?#'),
    ('HTTP_PROXY', 'http://outside.invalid:3128'),
    ('HTTPS_PROXY', 'http://outside.invalid:3128'),
    ('ALL_PROXY', 'socks5://outside.invalid:1080'),
    ('http_proxy', 'http://outside.invalid:3128'),
    ('https_proxy', 'http://outside.invalid:3128'),
    ('all_proxy', 'socks5://outside.invalid:1080'),
])
def test_recovery_drill_refuses_nonisolated_settings(key, value):
    values = environment()
    values[key] = value
    with pytest.raises(RuntimeError, match='isolated'):
        validate_environment(values)


def test_recovery_drill_accepts_only_its_fixture_database_pair():
    assert validate_environment(environment()) == (
        'api_cache_http_drill_recovery_source',
        'api_cache_http_drill_recovery_restored',
    )


def test_recovery_drill_accepts_empty_overrides_and_restored_role():
    values = dict(environment(), RPC_USER='', RPC_PASSWORD='', DB_PASSWORD='')
    values['DB_NAME'] = 'api_cache_http_drill_recovery_restored'
    assert validate_environment(values)[1] == values['DB_NAME']


@pytest.mark.parametrize('key', ['RPC_USER', 'RPC_PASSWORD'])
def test_bad_rpc_credentials_fail_before_runtime_import_or_connection(monkeypatch, key):
    values = dict(environment(), **{key: 'fixture@outside.invalid:8332/path?#'})
    worker = Mock()
    original_import = builtins.__import__

    def reject_runtime_import(name, *args, **kwargs):
        if name in ('pymongo', 'mongoengine', 'db.db', 'daemon'):
            raise AssertionError('Unsafe credentials reached a runtime import')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(os, 'environ', values)
    monkeypatch.setattr(sys, 'argv', ['recovery_smoke.py', '--child', 'normal'])
    monkeypatch.setattr(recovery_smoke, 'child', worker)
    monkeypatch.setattr(builtins, '__import__', reject_runtime_import)
    with pytest.raises(RuntimeError, match='isolated') as error:
        recovery_smoke.main()
    assert values[key] not in str(error.value)
    worker.assert_not_called()


@pytest.fixture
def copies(connection):
    prefix = 'api_cache_tests_' + uuid.uuid4().hex
    source = connection[prefix + '_source']
    restored = connection[prefix + '_restored']
    try:
        source.address.insert_one({'_id': 'sender', 'balance': '2.00000000'})
        source.address.create_index('balance')
        restored.address.insert_many([deepcopy(row) for row in source.address.find()])
        restored.address.create_index('balance')
        yield source, restored
    finally:
        connection.drop_database(source.name)
        connection.drop_database(restored.name)


def test_application_digest_ignores_derived_cache_metadata(copies):
    source, restored = copies
    source.cache_state.insert_one({'_id': 'blockchain', 'ready': True})
    source.query_cache_v2.insert_one({'_id': 'stale', 'result': []})
    source.request_cache.insert_one({'_id': 'legacy', 'result': []})
    assert application_digest(source) == application_digest(restored)
    verify_restored_copy(source, restored)


@pytest.mark.parametrize('collection', ['cache_state', 'query_cache_v2', 'request_cache'])
def test_restored_copy_must_not_reuse_cache_metadata(copies, collection):
    source, restored = copies
    restored[collection].insert_one({'_id': 'stale'})
    with pytest.raises(RuntimeError, match='cache'):
        verify_restored_copy(source, restored)


def test_restored_copy_rejects_changed_application_data(copies):
    source, restored = copies
    restored.address.update_one({'_id': 'sender'}, {'$set': {'balance': '9.00000000'}})
    with pytest.raises(RuntimeError, match='application'):
        verify_restored_copy(source, restored)


def test_restored_copy_rejects_a_missing_application_index(copies):
    source, restored = copies
    restored.address.drop_index('balance_1')
    with pytest.raises(RuntimeError, match='application'):
        verify_restored_copy(source, restored)


def test_restored_copy_rejects_changed_bson_integer_types(copies):
    from bson.int64 import Int64

    source, restored = copies
    source.address.update_one({'_id': 'sender'}, {'$set': {'counter': Int64(7)}})
    restored.address.update_one({'_id': 'sender'}, {'$set': {'counter': 7}})
    with pytest.raises(RuntimeError, match='application'):
        verify_restored_copy(source, restored)


def test_restored_copy_rejects_changed_embedded_document_order(copies):
    source, restored = copies
    source.address.update_one({'_id': 'sender'}, {'$set': {'ordered': {'a': 1, 'b': 2}}})
    restored.address.update_one({'_id': 'sender'}, {'$set': {'ordered': {'b': 2, 'a': 1}}})
    with pytest.raises(RuntimeError, match='application'):
        verify_restored_copy(source, restored)


def test_restored_copy_rejects_changed_text_index_options(copies):
    source, restored = copies
    source.address.create_index([('description', 'text')], weights={'description': 2})
    restored.address.create_index([('description', 'text')], weights={'description': 3})
    with pytest.raises(RuntimeError, match='application'):
        verify_restored_copy(source, restored)


def test_restored_copy_rejects_changed_collection_options(copies):
    source, restored = copies
    restored.command('collMod', 'address', validator={'balance': {'$type': 'string'}})
    with pytest.raises(RuntimeError, match='application'):
        verify_restored_copy(source, restored)


def test_optimized_python_must_not_skip_fixture_verification():
    root = Path(__file__).resolve().parents[1]
    code = '''
import sys
from unittest.mock import MagicMock
from recovery_smoke import verify_fixture
database = MagicMock()
database.__getitem__.return_value.count_documents.return_value = 999
try:
    verify_fixture(database, 0)
except RuntimeError:
    sys.exit(0)
sys.exit(1)
'''
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(root / 'src'), str(root / 'tests'))))
    result = subprocess.run([sys.executable, '-O', '-c', code], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert result.returncode == 0, result.stderr.decode()


def test_stopped_worker_check_rejects_data_changed_before_failure(copies):
    from recovery_smoke import verify_stopped_state

    source, _ = copies
    source.cache_state.insert_one({'_id': 'blockchain', 'ready': False, 'writes_complete': False})
    expected = application_digest(source)
    source.address.delete_one({'_id': 'sender'})
    with pytest.raises(RuntimeError, match='application'):
        verify_stopped_state(source, expected)


@pytest.mark.parametrize('field', ['ready', 'writes_complete'])
def test_stopped_worker_check_rejects_state_changed_before_failure(copies, field):
    from recovery_smoke import verify_stopped_state

    source, _ = copies
    source.cache_state.insert_one({'_id': 'blockchain', 'ready': False, 'writes_complete': False})
    expected = application_digest(source)
    source.cache_state.update_one({'_id': 'blockchain'}, {'$set': {field: True}})
    with pytest.raises(RuntimeError, match='readiness'):
        verify_stopped_state(source, expected)


DOCKER_STUB = '''
docker() {
  case "$1" in
    context)
      if [[ "$FAKE_FAILURE" == remote-context ]]; then echo ssh://outside.invalid;
      else echo unix:///var/run/docker.sock; fi ;;
    network)
      if [[ "$FAKE_FAILURE" == external-network ]]; then echo false;
      else echo true; fi ;;
    inspect)
      case "${@: -1}" in
        *State.Running*)
          if [[ "$FAKE_FAILURE" == stopped ]]; then echo false; else echo true; fi ;;
        *PublishAllPorts*)
          if [[ "$FAKE_FAILURE" == publish-all ]]; then echo true; else echo false; fi ;;
        *PortBindings*)
          if [[ "$FAKE_FAILURE" == published ]]; then echo 1; else echo 0; fi ;;
        *'len .NetworkSettings.Networks'*)
          if [[ "$FAKE_FAILURE" == multi-network ]]; then echo 2; else echo 1; fi ;;
        *)
          if [[ "$FAKE_FAILURE" != disconnected ]]; then echo isolated-network-id; fi ;;
      esac ;;
    image) [[ "$FAKE_FAILURE" != missing-image ]] ;;
    run|exec)
      if [[ "$FAKE_FAILURE" != none ]]; then
        echo 'Unexpected database operation after failed preflight' >&2; return 93;
      fi
      printf 'FAKE_DOCKER %s\\n' "$*" >&2 ;;
    *) echo 'Unexpected Docker command' >&2; return 94 ;;
  esac
}
source "$1"
'''


def run_shell_guard(failure, **overrides):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, TEST_DATABASE='api_cache_http_guard', TEST_NETWORK='isolated-fixture',
               API_IMAGE='api-candidate', DOCKER_HOST='unix:///var/run/docker.sock',
               DOCKER_CONTEXT='', DOCKER_TLS_VERIFY='', DOCKER_CERT_PATH='', FAKE_FAILURE=failure)
    env.update(overrides)
    return subprocess.run(['bash', '-c', DOCKER_STUB, 'recovery-guard',
                           str(root / 'tests/recovery_smoke.sh')], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=10)


@pytest.mark.parametrize('failure,overrides,message', [
    ('none', {'TEST_DATABASE': 'blockchain'}, 'fixture prefix'),
    ('none', {'TEST_DATABASE': 'api_cache_http_' + 'x' * 33}, 'fixture prefix'),
    ('none', {'TEST_NETWORK': 'fixture"}}unsafe'}, 'literal Docker network'),
    ('none', {'API_IMAGE': '--help'}, 'literal local image'),
    ('none', {'DOCKER_HOST': 'tcp://outside.invalid:2375'}, 'local Docker socket'),
    ('none', {'DOCKER_TLS_VERIFY': '1'}, 'without TLS credentials'),
    ('none', {'DOCKER_CERT_PATH': '/private/certs'}, 'without TLS credentials'),
    ('remote-context', {'DOCKER_CONTEXT': 'remote'}, 'local Docker socket'),
    ('external-network', {}, 'internal Docker network'),
    ('stopped', {}, 'Fixture containers must'),
    ('publish-all', {}, 'Fixture containers must'),
    ('published', {}, 'Fixture containers must'),
    ('multi-network', {}, 'Fixture containers must'),
    ('disconnected', {}, 'Fixture containers must'),
    ('missing-image', {}, 'existing local candidate image'),
])
def test_shell_guard_rejects_unsafe_settings_before_database_work(failure, overrides, message):
    result = run_shell_guard(failure, **overrides)
    assert result.returncode == 1, result.stderr
    assert message in result.stderr
    assert 'Unexpected database operation' not in result.stderr
    assert 'FAKE_DOCKER' not in result.stderr


def test_shell_uses_local_image_forced_fixture_env_and_application_only_backup():
    result = run_shell_guard('none')
    assert result.returncode == 0, result.stderr
    calls = result.stderr.splitlines()
    runs = [line for line in calls if line.startswith('FAKE_DOCKER run ')]
    assert len(runs) == 2
    assert runs[0].endswith('/checks/recovery_smoke.py prepare')
    assert runs[1].endswith('/checks/recovery_smoke.py exercise')
    for call in runs:
        assert '--pull=never' in call
        assert '--network isolated-fixture' in call
        for variable in ('DB_USERNAME', 'DB_PASSWORD', 'DB_URI_PARAMS', 'RPC_USER', 'RPC_PASSWORD',
                         'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                         'http_proxy', 'https_proxy', 'all_proxy'):
            assert '-e ' + variable + '= ' in call
    dump = next(line for line in calls if ' mongodump ' in line)
    restore = next(line for line in calls if ' mongorestore ' in line)
    for name in ('cache_state', 'query_cache_v2', 'request_cache'):
        assert '--excludeCollection=' + name in dump
    assert '--host=127.0.0.1 --port=27017' in dump
    assert '--archive=' in dump and '--gzip' in dump
    assert '--nsFrom=api_cache_http_guard_recovery_source.*' in restore
    assert '--nsTo=api_cache_http_guard_recovery_restored.*' in restore
    assert '--stopOnError' in restore and '--drop' not in restore
    assert 'service_smoke' not in result.stderr


def fixture_database(height):
    from db.db_utils import create_block
    from fake_rpc import BLOCKS

    rows = {
        'block': [create_block(deepcopy(block)).to_mongo() for block in BLOCKS[:height + 1]],
        'address': [{'_id': 'sender', 'balance': 2 if height == 0 else 0}],
        'utxo': [{'_id': 'origin0' if height == 0 else 'spend0',
                  'address': 'sender' if height == 0 else 'recipient',
                  'value': 2.0 if height == 0 else 3.0}],
    }
    if height == 1:
        rows['address'].append({'_id': 'recipient', 'balance': 3})
    collections = {}
    for name, documents in rows.items():
        collection = MagicMock()
        collection.count_documents.side_effect = lambda query, documents=documents: len(documents)
        collection.find_one.side_effect = lambda query, documents=documents: next(
            (row for row in documents if all(row.get(key) == value for key, value in query.items())), None)
        collections[name] = collection
    database = MagicMock()
    database.__getitem__.side_effect = collections.__getitem__
    return database, rows


@pytest.mark.parametrize('height', [0, 1])
def test_fixture_verification_accepts_btcv_model_serialization(height):
    database, _ = fixture_database(height)
    recovery_smoke.verify_fixture(database, height)


@pytest.mark.parametrize('field,value', [
    ('type', 'ordinary'), ('status', 'confirmed'), ('hash', 'tampered'),
    ('vin', []), ('vout', []),
])
def test_fixture_verification_rejects_btcv_alert_metadata_tampering(field, value):
    database, rows = fixture_database(1)
    rows['block'][0]['atx'][0][field] = value
    with pytest.raises(RuntimeError, match='alert metadata'):
        recovery_smoke.verify_fixture(database, 1)


def test_fixture_verification_rejects_missing_alert_metadata():
    database, rows = fixture_database(1)
    rows['block'][0]['atx'] = []
    with pytest.raises(RuntimeError, match='alert metadata'):
        recovery_smoke.verify_fixture(database, 1)


@pytest.mark.parametrize('collection,extra', [
    ('address', {'_id': 'alert-recipient', 'balance': 97}),
    ('utxo', {'_id': 'alert-00', 'address': 'alert-recipient', 'value': 97}),
])
def test_fixture_verification_rejects_spendable_alert_outputs(collection, extra):
    database, rows = fixture_database(1)
    rows[collection].append(extra)
    with pytest.raises(RuntimeError, match='address count|UTXO count'):
        recovery_smoke.verify_fixture(database, 1)


@pytest.mark.parametrize('collection,field,value', [
    ('block', '_id', 'forked-0'), ('block', 'previousblockhash', 'forked-parent'),
    ('address', 'balance', 99), ('utxo', 'value', 99), ('utxo', 'address', 'wrong-owner'),
])
def test_fixture_oracle_rejects_wrong_tip_balance_or_utxo(collection, field, value):
    database, rows = fixture_database(1)
    rows[collection][0][field] = value
    with pytest.raises(RuntimeError, match='fixture'):
        recovery_smoke.verify_fixture(database, 1)


@pytest.mark.parametrize('base_url,route', [
    ('http://outside.invalid:18080', '/status'),
    ('http://127.0.0.1:18080@outside.invalid', '/status'),
    ('http://127.0.0.1:18080/redirect', '/status'),
    ('https://127.0.0.1:18080', '/status'),
    ('http://127.0.0.1:80', '/status'),
    ('http://127.0.0.1:99999', '/status'),
    ('http://127.0.0.1:18080', '//outside.invalid'),
    ('http://127.0.0.1:18080', '/status?url=http://outside.invalid'),
])
def test_api_guard_rejects_nonfixture_urls_before_request(base_url, route):
    session = Mock()
    with pytest.raises(RuntimeError, match='loopback'):
        recovery_smoke.read_api(session, base_url, route)
    session.get.assert_not_called()


def test_api_guard_disables_redirects_and_checks_http_status():
    session = Mock()
    session.get.return_value.status_code = 302
    with pytest.raises(RuntimeError, match='non-200'):
        recovery_smoke.read_api(session, 'http://127.0.0.1:18080', '/status')
    session.get.assert_called_once_with('http://127.0.0.1:18080/status',
                                        timeout=5, allow_redirects=False)


@pytest.mark.parametrize('status,count', [
    ({'height': 0, 'hashrate': 123, 'total_coin_supply': 3.0}, 1),
    ({'height': 1, 'hashrate': 123, 'total_coin_supply': 100.0}, 1),
    ({'height': 1, 'hashrate': 123, 'total_coin_supply': 3.0}, 2),
])
def test_api_oracle_rejects_wrong_restored_tip_balances_or_wallet_count(status, count):
    values = {'/status': status, '/nonemptywalletsnumber': count}
    with pytest.raises(RuntimeError, match='independently known fixture'):
        recovery_smoke.verify_api(values.__getitem__)


def test_api_cleanup_kills_only_its_process_after_bounded_wait():
    process = Mock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired('gunicorn', 10), 0]
    recovery_smoke.stop_owned_process(process)
    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.call_count == 2
    process.wait.assert_called_with(timeout=10)


def test_api_cleanup_does_not_signal_an_exited_process():
    process = Mock()
    process.poll.return_value = 0
    recovery_smoke.stop_owned_process(process)
    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def mock_api_process(monkeypatch):
    values = environment()
    values['DB_NAME'] = 'api_cache_http_drill_recovery_restored'
    monkeypatch.setattr(os, 'environ', values)
    listener = MagicMock()
    listener.getsockname.return_value = ('127.0.0.1', 18080)
    listener.fileno.return_value = 42
    listener.__enter__.return_value = listener
    monkeypatch.setattr(recovery_smoke.socket, 'socket', Mock(return_value=listener))
    output = MagicMock()
    output.__enter__.return_value = output
    monkeypatch.setattr(recovery_smoke.tempfile, 'TemporaryFile', Mock(return_value=output))
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value.status_code = 200
    session.get.return_value.json.return_value = {
        'height': 1, 'hashrate': 123, 'total_coin_supply': 3.0,
    }
    monkeypatch.setattr(recovery_smoke.requests, 'Session', Mock(return_value=session))
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(recovery_smoke.subprocess, 'Popen', popen)
    return values['DB_NAME'], listener, session, process, popen


def test_restored_api_owns_loopback_listener_and_cleans_up_on_caller_failure(monkeypatch):
    name, listener, session, process, popen = mock_api_process(monkeypatch)
    with pytest.raises(RuntimeError, match='consumer failure'):
        with recovery_smoke.restored_api(name):
            raise RuntimeError('consumer failure')
    listener.bind.assert_called_once_with(('127.0.0.1', 0))
    assert session.trust_env is False
    args, kwargs = popen.call_args
    assert args[0] == [sys.executable, '-m', 'gunicorn', '-w', '1', '--bind',
                       'fd://42', '--access-logfile', '-', 'api:app']
    assert kwargs['cwd'] == '/app'
    assert kwargs['env']['DB_NAME'] == name
    assert kwargs['pass_fds'] == (42,)
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=10)


def test_restored_api_times_out_and_cleans_up_without_a_live_server(monkeypatch):
    name, _, session, process, _ = mock_api_process(monkeypatch)
    session.get.side_effect = recovery_smoke.requests.Timeout('fixture timeout')
    monkeypatch.setattr(recovery_smoke.time, 'monotonic', Mock(side_effect=[0, 31]))
    with pytest.raises(RuntimeError, match='startup timed out'):
        with recovery_smoke.restored_api(name):
            pytest.fail('Timed-out API must not be yielded')
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=10)


def test_restored_api_refuses_wrong_database_role_before_starting_process(monkeypatch):
    name, _, _, _, popen = mock_api_process(monkeypatch)
    with pytest.raises(RuntimeError, match='restored fixture database'):
        with recovery_smoke.restored_api(name.replace('_restored', '_source')):
            pytest.fail('Source database must never be exposed as the restored API')
    popen.assert_not_called()
