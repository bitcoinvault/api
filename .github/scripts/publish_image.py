"""Publish one source-bound image and verify registry manifests before reporting success."""
import json
import os
from pathlib import Path
import re
import subprocess


def docker(*args):
    return subprocess.run(
        ['docker', *args], check=True, stdout=subprocess.PIPE,
        text=True, timeout=300,
    ).stdout.strip()


def registry_digest(reference):
    manifest = json.loads(docker(
        'buildx', 'imagetools', 'inspect', '--format', '{{json .Manifest}}', reference,
    ))
    digest = manifest.get('digest') if isinstance(manifest, dict) else None
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Registry digest could not be verified')
    return digest


def publish(env):
    registry, repository, sha, build, summary = (
        env[key] for key in ('ECR_REGISTRY', 'ECR_REPOSITORY', 'IMAGE_TAG',
                             'BUILD_NAME', 'GITHUB_STEP_SUMMARY')
    )
    if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.eu-west-1\.amazonaws\.com', registry):
        raise ValueError('Unexpected registry')
    if registry.split('.')[0] != '401615164202' or repository != 'api':
        raise ValueError('Unexpected publication target')
    if not re.fullmatch(r'[0-9a-f]{40}', sha) or not build or not summary:
        raise ValueError('Missing publication input or invalid source SHA')
    revision = docker('image', 'inspect', '--format',
                      '{{index .Config.Labels "org.opencontainers.image.revision"}}', build)
    if revision != sha:
        raise ValueError('Image revision does not match source SHA')
    image = registry + '/' + repository
    docker('tag', build, image + ':' + sha)
    docker('push', image + ':' + sha)
    digest = registry_digest(image + ':' + sha)
    with Path(summary).open('a', encoding='utf-8') as receipt:
        receipt.write('- Published `{0}:{1}` as `{0}@{2}`\n'.format(image, sha, digest))
        receipt.flush()
        docker('tag', build, image + ':latest')
        docker('push', image + ':latest')
        if registry_digest(image + ':latest') != digest:
            raise ValueError('Registry latest digest differs from source SHA digest')
        receipt.write('- Verified `{0}:latest` as `{0}@{1}` from source `{2}`\n'.format(
            image, digest, sha))


if __name__ == '__main__':
    publish(os.environ)
