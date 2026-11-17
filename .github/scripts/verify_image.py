"""Verify source parity and runtime imports without network or application mounts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
PROBE = '''import hashlib,json,platform
from pathlib import Path
import flask, flask_mongoengine, mongoengine, pymongo, gunicorn
import blockchain.blockchain_analyzer
files={p.relative_to('/app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
       for p in Path('/app').rglob('*') if p.is_file()}
print(json.dumps({'files':files,'python':platform.python_version(),
                 'mongoengine':mongoengine.__version__,'pymongo':pymongo.version}))
'''


def verify(image):
    expected = {p.relative_to(ROOT / 'src').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT / 'src').rglob('*') if p.is_file()}
    if not expected:
        raise ValueError('Source directory is empty')
    run = subprocess.run([
        'docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--memory', '256m',
        '--cpus', '1', '--entrypoint', 'python', image, '-B', '-c', PROBE,
    ], check=True, capture_output=True, text=True, timeout=30)
    observed = json.loads(run.stdout)
    if observed.get('files') != expected:
        raise ValueError('Image source differs from the complete source directory')
    return {'source_files': len(expected), 'python': observed['python'],
            'mongoengine': observed['mongoengine'], 'pymongo': observed['pymongo']}


if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1])))
