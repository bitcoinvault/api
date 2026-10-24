# Isolated BTCV backup and recovery drill

This test runs the candidate image's actual daemon functions against synthetic
RPC fixtures and disposable MongoDB databases. It does not contact a live BTCV
node, export production data, publish an image or deploy an application.

## Verified mechanisms

1. Ingest a known first block into an empty source fixture and populate cache.
2. Run real `mongodump` and `mongorestore` into a different, empty database,
   excluding `cache_state`, `query_cache_v2` and legacy `request_cache`.
3. Compare application BSON documents, collection options and index definitions
   with the quiescent source. Independently check the fixture's known balances,
   UTXOs and canonical tip. Reject tampered data or restored cache metadata.
4. Kill an ingestion subprocess with SIGKILL after a block write but before
   address updates. A restart must reject the unfinished batch, without replay
   or certifying its generation as ready.
5. Use the already verified restored database, not a readiness reset on the
   damaged source. Kill its worker after completed writes but before canonical
   verification. Restart must verify the pending batch without double balances
   or changing its generation.
6. Simulate a conflicting tip. Readiness must be invalidated, application data
   preserved, and an unverified restart rejected.
7. Start actual Gunicorn against the restored database using an owned loopback
   listener. Check known status and wallet-count responses before and after the
   conflicting tip, without cache writes or readiness certification after failure.

MongoDB, the dump/restore tools, persisted state and subprocess termination are
real. The chain data, timing checkpoints and conflicting RPC response are
controlled fixtures. A nonempty BTCV alert transaction checks metadata/type/status
persistence without adding spendable balances or UTXOs. These checks do not
validate all BTCV alert/cancel/fast transaction semantics or the full vault protocol.

## Run locally

Build the application/test images and start the disposable MongoDB and RPC
fixtures from [the cache guide](cache-hardening.md#run-the-tests). The network
must be created with `docker network create --internal`, with no published
ports or additional fixture-network attachments. Do not reuse existing service
containers or substitute infrastructure hostnames.

Before the fixture teardown, run from the repository root:

```bash
TEST_NETWORK=api-cache-test-network \
TEST_DATABASE=api_cache_http_recovery_local \
API_IMAGE=api-cache-candidate:local \
bash tests/recovery_smoke.sh
```

The MongoDB and RPC containers must be named `api-cache-test-mongo` and
`api-cache-test-rpc`. Run fixtures using those fixed names sequentially on a
shared Docker host. Preflight must refuse an existing unrelated container;
never remove it just to obtain these names.

The script rejects a non-internal network, extra fixture network attachments,
published ports, a reused archive and invalid database prefixes. Its Python
worker rejects non-fixture database/RPC hostnames, credentials and URI options.
Reused nonempty databases are refused; select a fresh prefix rather than
deleting unknown data. The archive remains inside the disposable MongoDB
container and is removed after success. Databases remain inspectable there
until the task-owned container is removed.

CI runs this drill on MongoDB 4.2.24 and 6.0.13, before packaging the candidate.
The existing required `build` check still requires both matrix jobs to succeed.
The recovery log is uploaded with JUnit and coverage reports; those artifacts
contain only synthetic fixture data. Adding this workflow does not mean a CI
run or deployment has occurred for an unpublished local branch.

## Evidence and failure handling

Retain the exact source SHA/tree, image ID, MongoDB/tool versions, test command
and exit code, BSON baseline digest and the resulting recovery JSON/log.
Successful output must show matching restored documents/indexes, no restored
cache metadata, rejection of partial-write restart, verification without double
balances, refusal to restart after a conflicting tip, and correct restored-API
responses without certifying the failed cache state.

CI and the scripted cache-guide setup always remove their disposable containers
and network, on failure as well as success. They do not retain the fixture
database for inspection. Retain the recovery log, JUnit/coverage reports, source
identity and image ID before relying on that automated path for diagnosis.

If database inspection is required, plan a separate controlled local rerun with
fresh isolated fixtures and explicit cleanup ownership, rather than the
automatic-teardown wrapper. Keep the same safety guards, inspect before cleanup,
then remove only task-owned containers and networks. Do not reset readiness,
overwrite the source, or retry against a real database to make a failed check
pass. A harness failure is not evidence of production data corruption.

## Production gate stays open

The child processes import `update_analyzer()` and `update_blockchain()` from
the real image. This is not a test of BTCV's complete `daemon.py` service loop,
operator pause/acknowledgement, SIGTERM shutdown or container lifecycle. BTCV
does not gain ELCASH's newer graceful-boundary implementation from these tests.

The synthetic source is deliberately quiescent at a known valid boundary.
Before production use, inventory all writers and demonstrate an approved way
to obtain a completed application checkpoint, or independently verify/rebuild
the captured data. Stopping a legacy process, matching a tip or copying BSON
does not prove that historic balances and UTXOs are consistent.

An actual protected backup, compatible tools, authorized isolated restore
target, independently reconciled canonical checkpoint and accepted recovery
procedure are still required. Measure restoration and replay against agreed
recovery objectives; this small fixture gives no production RTO/RPO guarantee.
Keep MongoDB topology changes, legacy-cache deletion and production rollout out
of this test. Follow [the cache migration and rollback constraints](cache-hardening.md#recovery-and-rollback).

Reference: [MongoDB backup and restore tools](https://www.mongodb.com/docs/manual/tutorial/backup-and-restore-tools/).
