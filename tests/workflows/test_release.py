"""Release contracts and mocked publisher tests, without cloud or Docker calls."""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[2]
SHA = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64
spec = importlib.util.spec_from_file_location('publisher', ROOT / '.github/scripts/publish_image.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)
probe_spec = importlib.util.spec_from_file_location('image_probe', ROOT / '.github/scripts/verify_image.py')
image_probe = importlib.util.module_from_spec(probe_spec)
probe_spec.loader.exec_module(image_probe)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.load((ROOT / '.github/workflows/release.yml').read_text(),
                                  Loader=yaml.BaseLoader)
        self.job = self.workflow['jobs']['release']
        self.steps = self.job['steps']

    def test_only_master_push_can_publish_with_read_only_github_token(self):
        self.assertEqual(self.workflow['on'], {'push': {'branches': ['master']}})
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        self.assertNotIn('permissions', self.job)
        checkout = self.steps[0]
        self.assertEqual(checkout['with'], {'ref': '${{ github.sha }}', 'persist-credentials': 'false'})
        serialized = json.dumps(self.workflow)
        for value in ('TS_GH_TOKEN', 'semantic-release', 'id-token'):
            self.assertNotIn(value, serialized)

    def test_pinned_runtime_build_and_tests_precede_push(self):
        build = self.steps[3]['run']
        self.assertIn('org.opencontainers.image.revision=$IMAGE_TAG', build)
        self.assertIn('--file Dockerfile.release --network none', build)
        self.assertIn('python3 .github/scripts/verify_image.py', build)
        self.assertIn('unittest discover', build)
        self.assertIn('target=/tests,readonly', build)
        self.assertEqual(self.workflow['env']['IMAGE_TAG'], '${{ github.sha }}')
        self.assertTrue(self.steps[1]['uses'].startswith('aws-actions/configure-aws-credentials@'))
        self.assertEqual(self.steps[-1]['run'], 'python3 .github/scripts/publish_image.py')
        recipe = (ROOT / 'Dockerfile.release').read_text()
        self.assertIn('api@sha256:32de18b5e37905dc0e3ec4169a684bde2bb429c6c3da374f9dc44ad5fb8cc0d8', recipe)
        self.assertIn('RUN rm -rf /app\nWORKDIR /app\nCOPY src .', recipe)
        self.assertNotIn('pip install', recipe)

    def test_existing_aws_contract_is_preserved_with_one_target(self):
        inputs = self.steps[1]['with']
        self.assertEqual(inputs['aws-access-key-id'], '${{ secrets.CI_DEPLOYER_AWS_ACCESS_KEY_ID }}')
        self.assertEqual(inputs['aws-secret-access-key'], '${{ secrets.CI_DEPLOYER_AWS_SECRET_ACCESS_KEY }}')
        self.assertEqual(inputs['role-to-assume'], '${{ secrets.CI_DEPLOYER_AWS_ROLE_TO_ASSUME }}')
        self.assertEqual(inputs['aws-region'], 'eu-west-1')
        self.assertEqual(inputs['allowed-account-ids'], '401615164202')
        self.assertEqual(inputs['unset-current-credentials'], 'true')
        self.assertEqual(self.steps[2]['env']['DOCKER_CONFIG'], self.steps[4]['env']['DOCKER_CONFIG'])
        self.assertEqual(self.steps[3]['env']['DOCKER_CONFIG'], self.steps[4]['env']['DOCKER_CONFIG'])
        self.assertEqual(self.steps[4]['env']['ECR_REGISTRY'], '${{ steps.login-ecr.outputs.registry }}')

    def test_serial_bounded_pinned_and_fail_closed(self):
        self.assertEqual(self.workflow['concurrency'], {
            'group': 'api-image-publication', 'cancel-in-progress': 'false'})
        self.assertLessEqual(int(self.job['timeout-minutes']), 30)
        for step in self.steps:
            if 'uses' in step:
                self.assertRegex(step['uses'], r'^[\w-]+/[\w-]+@[0-9a-f]{40}$')
            self.assertNotIn('continue-on-error', step)
            self.assertNotIn('if', step)
        self.assertNotIn('continue-on-error', self.job)

    def test_pr_checks_use_an_isolated_python_environment(self):
        workflow = yaml.load((ROOT / '.github/workflows/publication-checks.yml').read_text(),
                             Loader=yaml.BaseLoader)
        steps = workflow['jobs']['publication-tests']['steps']
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertNotIn('permissions', workflow['jobs']['publication-tests'])
        self.assertIn('python3 -m venv "$RUNNER_TEMP/publication-checks"', steps[1]['run'])
        python = '"$RUNNER_TEMP/publication-checks/bin/python"'
        self.assertIn(python + ' -m pip install PyYAML==6.0.2', steps[1]['run'])
        self.assertTrue(steps[2]['run'].startswith(python + ' -m unittest'))


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.summary = Path(self.temp.name) / 'summary.md'
        self.env = dict(ECR_REGISTRY='401615164202.dkr.ecr.eu-west-1.amazonaws.com',
                        ECR_REPOSITORY='api', IMAGE_TAG=SHA, BUILD_NAME='candidate',
                        GITHUB_STEP_SUMMARY=str(self.summary))
        self.image = self.env['ECR_REGISTRY'] + '/api'
        self.responses = [SHA, '', '', json.dumps({'digest': DIGEST}), '', '',
                          json.dumps({'digest': DIGEST})]

    def run_publish(self, responses=None):
        with patch.object(publisher, 'docker', side_effect=responses or self.responses) as run:
            publisher.publish(self.env)
        return run

    def test_source_then_latest_and_two_verified_receipts(self):
        run = self.run_publish()
        calls = [call.args for call in run.call_args_list]
        self.assertEqual([call for call in calls if call[0] == 'push'],
                         [('push', self.image + ':' + SHA), ('push', self.image + ':latest')])
        self.assertEqual(self.summary.read_text().count(DIGEST), 2)

    def test_docker_wrapper_uses_checked_bounded_argv(self):
        result = subprocess.CompletedProcess([], 0, ' digest\n')
        with patch.object(publisher.subprocess, 'run', return_value=result) as run:
            self.assertEqual(publisher.docker('push', 'image:sha'), 'digest')
        run.assert_called_once_with(['docker', 'push', 'image:sha'], check=True,
                                    stdout=subprocess.PIPE, text=True, timeout=300)

    def test_docker_wrapper_propagates_unknown_and_failed_results(self):
        for error in (subprocess.TimeoutExpired(['docker'], 300), OSError('not found'),
                      subprocess.CalledProcessError(1, ['docker'])):
            with self.subTest(error=type(error).__name__), patch.object(
                    publisher.subprocess, 'run', side_effect=error):
                with self.assertRaises(type(error)):
                    publisher.docker('push', 'image:sha')

    def test_invalid_inputs_fail_before_docker(self):
        for key, value in [('IMAGE_TAG', 'latest'), ('IMAGE_TAG', 'x' * 40),
                           ('ECR_REGISTRY', '111111111111.dkr.ecr.eu-west-1.amazonaws.com'),
                           ('ECR_REPOSITORY', 'other'), ('GITHUB_STEP_SUMMARY', '')]:
            with self.subTest(key=key, value=value), patch.object(publisher, 'docker') as run:
                env = dict(self.env, **{key: value})
                with self.assertRaises(ValueError):
                    publisher.publish(env)
                run.assert_not_called()

    def test_revision_mismatch_prevents_publication(self):
        with patch.object(publisher, 'docker', return_value='old') as run:
            with self.assertRaises(ValueError):
                publisher.publish(self.env)
            self.assertEqual(run.call_count, 1)
        self.assertFalse(self.summary.exists())

    def test_each_command_failure_stops_without_false_receipt(self):
        for index in range(7):
            self.summary.unlink(missing_ok=True)
            responses = list(self.responses)
            responses[index] = subprocess.CalledProcessError(42, ['docker'])
            with patch.object(publisher, 'docker', side_effect=responses) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    publisher.publish(self.env)
                self.assertEqual(run.call_count, index + 1)
            text = self.summary.read_text() if self.summary.exists() else ''
            self.assertNotIn('Verified', text)
            self.assertEqual('Published' in text, index >= 4)

    def test_bad_registry_manifest_never_promotes_latest(self):
        for value in ('not-json', '{}', 'null', '{"digest":null}', '{"digest":"bad"}'):
            with self.subTest(value=value), patch.object(publisher, 'docker',
                    side_effect=[SHA, '', '', value]) as run:
                with self.assertRaises((ValueError, TypeError)):
                    publisher.publish(self.env)
                self.assertEqual(run.call_count, 4)

    def test_latest_mismatch_keeps_source_receipt_but_fails(self):
        responses = list(self.responses)
        responses[-1] = json.dumps({'digest': 'sha256:' + 'c' * 64})
        with self.assertRaises(ValueError):
            self.run_publish(responses)
        text = self.summary.read_text()
        self.assertIn('Published', text)
        self.assertNotIn('Verified', text)


class ImageProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'src').mkdir()
        (self.root / 'src/api.py').write_bytes(b'print(1)\n')
        self.files = {'api.py': hashlib.sha256(b'print(1)\n').hexdigest()}

    def result(self, files):
        return subprocess.CompletedProcess([], 0, json.dumps({
            'files': files, 'python': '3.8.12', 'mongoengine': '0.23.1', 'pymongo': '3.12.3'}))

    def test_complete_source_and_imports_required_without_host_mount(self):
        with patch.object(image_probe, 'ROOT', self.root), patch.object(
                image_probe.subprocess, 'run', return_value=self.result(self.files)) as run:
            result = image_probe.verify('candidate')
        self.assertEqual(result['source_files'], 1)
        args = run.call_args.args[0]
        self.assertIn('none', args)
        self.assertIn('--read-only', args)
        self.assertNotIn('--mount', args)
        self.assertIn('import flask, flask_mongoengine', args[-1])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)
        self.assertTrue(run.call_args.kwargs['check'])

    def test_missing_stale_or_modified_image_files_are_rejected(self):
        for files in ({}, {'api.py': 'bad'}, dict(self.files, stale='extra')):
            with self.subTest(files=files), patch.object(image_probe, 'ROOT', self.root), patch.object(
                    image_probe.subprocess, 'run', return_value=self.result(files)):
                with self.assertRaises(ValueError):
                    image_probe.verify('candidate')

    def test_empty_checkout_is_rejected_before_container(self):
        (self.root / 'src/api.py').unlink()
        with patch.object(image_probe, 'ROOT', self.root), patch.object(image_probe.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                image_probe.verify('candidate')
            run.assert_not_called()

    def test_import_failure_timeout_and_invalid_output_fail_closed(self):
        for error in (subprocess.CalledProcessError(1, ['docker']),
                      subprocess.TimeoutExpired(['docker'], 30)):
            with self.subTest(error=type(error).__name__), patch.object(image_probe, 'ROOT', self.root), patch.object(
                    image_probe.subprocess, 'run', side_effect=error):
                with self.assertRaises(type(error)):
                    image_probe.verify('candidate')
        with patch.object(image_probe, 'ROOT', self.root), patch.object(
                image_probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'not-json')):
            with self.assertRaises(ValueError):
                image_probe.verify('candidate')


if __name__ == '__main__':
    unittest.main()
