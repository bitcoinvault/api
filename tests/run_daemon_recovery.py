"""Run daemon accounting tests on a local Docker engine without pulling images.

    python tests/run_daemon_recovery.py worktree --runtime-image APP --mongo-image MONGO
    python tests/run_daemon_recovery.py baseline --revision FULL_SHA --runtime-image APP --mongo-image MONGO

The runtime must provide sh, tar, python and the project's Python dependencies.
Baseline runs only the mid-chunk regression and returns its actual exit code.
"""
import argparse
import hashlib
import io
import os
import re
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE = "test_daemon_recovery.py"


def sources(revision):
    if revision:
        raw = subprocess.check_output(["git", "archive", "--format=tar", revision, "src"], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            files = {m.name: archive.extractfile(m).read() for m in archive
                     if m.isfile() and m.name.endswith(".py")}
    else:
        files = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in (ROOT / "src").rglob("*.py")}
    files[CASE] = (ROOT / "tests" / CASE).read_bytes()
    if not revision:
        for name in ("test_vault_journal.py", "test_vault_journal_mongo.py", "test_legacy_guard.py"):
            files[name] = (ROOT / "tests" / name).read_bytes()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
            if name in ("src/daemon.py", "src/db/block_recovery.py", CASE):
                print(name, hashlib.sha256(data).hexdigest())
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("baseline", "worktree"))
    parser.add_argument("--context", help="local Unix socket or local Windows named-pipe context")
    parser.add_argument("--runtime-image", required=True, help="locally available API dependency image")
    parser.add_argument("--mongo-image", required=True, help="locally available standalone mongo image")
    parser.add_argument("--revision", help="full baseline Git commit SHA")
    args = parser.parse_args()
    if args.source == "baseline" and not re.fullmatch("[0-9a-f]{40}", args.revision or ""):
        parser.error("baseline requires --revision with a full Git commit SHA")
    if args.source == "worktree" and args.revision:
        parser.error("--revision is for baseline only")
    payload = sources(args.revision)
    run = "btcv-accounting-" + uuid.uuid4().hex
    label = "btcv.accounting.run=" + run
    if not args.context and not os.environ.get("DOCKER_CONTEXT") and os.environ.get("DOCKER_HOST"):
        endpoint = os.environ["DOCKER_HOST"]
    else:
        context = args.context or subprocess.check_output(["docker", "context", "show"], text=True).strip()
        endpoint = subprocess.check_output(
            ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}", "--", context],
            text=True).strip()
    if not re.fullmatch(r"unix:///(?!/).+|npipe:////\./pipe/[A-Za-z0-9_.-]+", endpoint):
        parser.error("refusing Docker endpoint: use a local unix socket or local named pipe")
    # Pin the validated socket rather than trusting later context or environment changes.
    cli = ["docker", "--host", endpoint]
    env = {k: v for k, v in os.environ.items() if k not in ("DOCKER_HOST", "DOCKER_CONTEXT")}

    def docker(*args, **kw):
        return subprocess.run(cli + list(args), check=True, env=env, **kw)

    def image_id(ref):
        identity = docker("image", "inspect", "--format", "{{.Id}}", "--", ref,
                          capture_output=True, text=True).stdout.strip()
        if not re.fullmatch("sha256:[0-9a-f]{64}", identity):
            raise ValueError("invalid local Docker image ID")
        return identity

    runtime, mongo_image = image_id(args.runtime_image), image_id(args.mongo_image)
    print("run=%s source=%s source_tar_sha256=%s" %
          (run, args.source, hashlib.sha256(payload).hexdigest()), flush=True)
    net, mongo, app = run + "-net", run + "-mongo", run + "-test"
    try:
        docker("network", "create", "--internal", "--label", label, net)
        docker("run", "-d", "--pull=never", "--network", net, "--network-alias", "recovery-mongo",
               "--label", label, "--name", mongo, "--tmpfs", "/data/db:rw,size=512m",
               "--tmpfs", "/data/configdb:rw,size=32m", mongo_image, "mongod", "--bind_ip_all",
               "--wiredTigerCacheSizeGB", "0.25")
        tests = (CASE + " -v RecoveryTest.test_midchunk_restart" if args.source == "baseline"
                 else "-m unittest discover -s . -p 'test_*.py' -v")
        script = ("mkdir /tmp/recovery && tar -xf - -C /tmp/recovery && cd /tmp/recovery && "
                  "exec python -B " + tests)
        proc = subprocess.run(cli + ["run", "-i", "--pull=never", "--name", app, "--network", net,
                              "--label", label, "--read-only", "--tmpfs", "/tmp:rw,size=64m",
                              "-e", "DAEMON_RECOVERY_TEST_MONGO=recovery-mongo", "-e", "PYTHONPATH=/tmp/recovery/src",
                              "--entrypoint", "sh", runtime, "-ec", script], input=payload, env=env, timeout=180)
        return proc.returncode
    finally:
        # Filter by unguessable run label even after an ambiguous start; never remove by prefix.
        for kind, listing, removal in [
            ("container", ["ps", "-aq"], ["rm", "-fv"]),
            ("network", ["network", "ls", "-q"], ["network", "rm"]),
        ]:
            query = listing + ["--filter", "label=" + label]
            owned = docker(*query, capture_output=True, text=True).stdout.split()
            if owned:
                docker(*removal, *owned)
            if docker(*query, capture_output=True, text=True).stdout.strip():
                raise RuntimeError("owned %s cleanup incomplete" % kind)
        print("cleanup: no owned containers or networks; Mongo data/config used tmpfs", flush=True)


if __name__ == "__main__":
    sys.exit(main())
