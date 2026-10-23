# API cache hardening

Base: production image/source `81edd479c0205b0996e8f0318bb34db2326f1e7d`.
This change is prepared locally. It has not been deployed to production.

## Scope

- Stop consulting or adding to the legacy `request_cache` collection.
- Store new results in `query_cache_v2`, using the built-in unique `_id` index.
- Include the database/collection, query type, parameters, pipeline and data
  generation in the key. BSON encoding preserves order-sensitive pipeline
  documents such as compound sorts. Empty results are valid cache hits.
- Expire entries after one hour. The TTL index is created on the new collection,
  not on the existing large collection. Application reads also check expiry,
  independently of the asynchronous MongoDB TTL cleanup.
- Cache total supply and nonempty-address counts without changing JSON shapes.
- Constrain rebuild dependencies to versions observed in the deployed image.
  Historical library vulnerabilities are not addressed by this incident fix.
- Check every ingested block extends the preceding verified hash. Publish a new
  generation only after the daemon finishes its address updates and checks its
  stored tip against the node. Cache hits do not write to MongoDB.
- Leave interrupted updates and detected reorgs fail-closed. This is a safety
  boundary, not an implementation of automatic blockchain recovery.

The old cache is deliberately left intact for rollback. Deleting it is a separate
approved maintenance operation. This patch does not change miners or GitOps.

## Limitations and operational change

The daemon persists blocks/UTXOs before all address balances have been updated.
Previously ignored write failures could therefore leave inconsistent statistics.
Write failures now propagate, and an unfinished `cache_state` marker prevents a
restart from silently resuming and publishing that data as a completed snapshot.
A same-height node/stored-tip mismatch also requires verified recovery.

Do not deploy without accepting this fail-closed behavior and having an operator
available to handle an interrupted update. Do not reset the marker merely to make
the daemon start. Cache safety does not repair a pre-existing inconsistent DB.
Reads during a normal update preserve the existing uncached behavior; this is
not transaction-level snapshot isolation for the HTTP API.

Concurrent cold requests can still compute the same result independently; atomic
upserts prevent duplicate rows, but do not provide a distributed computation lock.
The legacy data and other uncached queries may still require separate maintenance.

## Local verification

The tests use a real, isolated MongoDB 6 container and generated database names.
No production connection string is used. The test setup rejects non-local hosts.

```powershell
docker network create gw-cache-tests-20261023
docker run --detach --rm --name gw-cache-mongo-20261023 --network gw-cache-tests-20261023 mongo:6.0.13 --bind_ip_all --quiet
docker build -f tests/Dockerfile -t gw-cache-test:py38-20261023 .
docker run --rm --network gw-cache-tests-20261023 -e TEST_MONGODB_URI=mongodb://gw-cache-mongo-20261023:27017 -e COVERAGE_FILE=/tmp/cache.coverage --mount "type=bind,source=$($PWD.Path),target=/work,readonly" gw-cache-test:py38-20261023 tests -q --cov=db.query_cache --cov-branch --cov-report=term-missing --cov-fail-under=80
```

Stop only the test resources created by this procedure after all test processes
finish. Do not reuse production credentials or restore production data for tests.

### Verification performed on 2026-10-23

- Initial regression suite against the unchanged source: 5 failed, 3 passed.
- Updated suite: 41 passed using Python 3.8, the pinned runtime libraries and a
  real isolated MongoDB 6.0.13. The new cache module reached 100% statement/branch
  coverage; this is not a whole-application coverage claim.
- The normal Dockerfile built successfully; `pip check` passed in that image.
- HTTP smoke used the built image's Gunicorn process, synthetic MongoDB data and
  a local RPC stub. Ten status requests plus ten address-count requests executed
  two address aggregations. A subsequent generation change returned fresh data.
- Independent review found mixed-fork ingestion, translated cache-write errors
  and order-sensitive cache-key issues. Regressions were added and fixed before
  rerunning the suite and image smoke.
- A real MongoDB ingestion test processes a synthetic coinbase and its spend,
  checks balances and UTXOs, and verifies publication of successive generations.
- An RPC failure before the first write preserves the previous ready generation;
  a failure after mutations leaves the state disabled pending verified recovery.
- No live chain integration, production rollout or production load test has been
  performed. Existing legacy aggregate/Dockerfile deprecation warnings remain.

The OSV advisory API was also queried for the observed runtime versions in both
projects. Existing versions of Flask, Jinja2, Werkzeug, certifi, dnspython,
gunicorn, idna, pymongo, requests and urllib3 have published advisories. This
patch preserves those deployed versions; it is not a dependency-security
upgrade or evidence that any advisory is exploitable in this deployment. The
legacy runtime remains a separate release/security risk requiring triage.

The final local candidate `gw-api-candidate:btcv-cache-20261023` has image ID
`sha256:da1894455d277c422b2911df99acf0ee2527887b4cc40fec3e330488248fbd30`.
Its five modified source files were hashed and matched to this worktree. The
scripts `tests/fake_rpc.py` and `tests/http_smoke.py` provide the local HTTP test.
Test containers were stopped after validation; no background monitoring remains.

## Proposed rollout, not performed

1. Review the complete API/daemon patch together. Build from the pinned source
   plus this patch, not from an unrelated newer branch.
2. Test the paired API and daemon on an isolated non-production database. Cover
   successful ingestion, interruption, reorg detection, rollback and HTTP shapes.
3. Confirm backups and an application-data-only recovery procedure. A restore
   must not restore `cache_state` or `query_cache_v2` from a dump.
   Verify the initial application data is consistent; new guards do not detect
   historical balance/UTXO corruption solely by comparing the tip hash.
4. In an approved window, roll out the daemon first. A pre-existing database has
   no ready marker; caching starts only after the next successful ingestion.
5. Verify the daemon completed an update, `cache_state.ready` is true, and the
   stored height/hash agrees with the node. Then roll out the API.
6. Confirm repeated identical requests use the `_id` index and reuse results.
   Compare worker timeouts, HTTP 5xx, MongoDB slow queries and CPU with baseline.
7. Do not remove old cache data in the rollout. Schedule cleanup independently.

## Interrupted update or reorg

Stop the affected daemon before recovery. Preserve evidence and a backup first.
Verify or rebuild the affected `block`, `address` and `utxo` data from a canonical
source in an isolated database. A height match alone is not a balance/UTXO check.
Never restore the new cache/state collections from a backup.

After application data has been independently verified and with the daemon still
stopped, an approved operator can remove only the `_id: "blockchain"` marker from
`cache_state`. Restart the daemon and verify a complete new block update creates
a fresh ready generation. Until then the API bypasses the cache. This procedure
does not authorize database changes or define an unverified restore as safe.

## Rollback

Roll back the API first, then the daemon, using the previously recorded digests
through the normal approved deployment path. Do not leave the new API running
with the old daemon: it does not invalidate generations. No legacy collection
was migrated or removed.
The previous code ignores the new cache collections, but also lacks the new
failure guards. Do not roll back to bypass a known data-integrity incident.
