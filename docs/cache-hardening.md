# Query cache and ingestion safety

## Behavior

The API stores computed query results in `query_cache_v2` instead of reading or
appending to the legacy `request_cache` collection. Existing HTTP response
formats are unchanged.

- A cache entry's unique `_id` hashes the database/collection, query type,
  parameters, BSON-encoded pipeline and completed data generation. BSON encoding
  preserves field order for compound sorts. Empty results are cacheable.
- Entries expire after one hour. Reads check expiry immediately; a TTL index
  removes expired entries asynchronously. Cache hits do not write to MongoDB.
- Supply and nonempty-address statistics reuse cached aggregations. Concurrent
  misses may still compute independently; atomic upserts prevent duplicate rows,
  not duplicate computation.
- The daemon invalidates the generation before its first write. It publishes a
  new ready generation only after address updates and canonical-chain checks.
  Fetched block heights and parent hashes are checked before insertion.
- An RPC failure before any write preserves the previous ready generation.
  An interrupted mutation or detected inconsistency requires verified recovery.
- Cache read/write failures fall back to the underlying query. Ingestion-state
  write failures propagate rather than silently certifying incomplete data.

The legacy cache is not migrated or deleted. Its cleanup is separate maintenance.

## Compatibility and limitations

**Operational change:** the daemon stops on unfinished updates or noncanonical
data instead of continuing with potentially inconsistent statistics. Accept this
behavior and establish a recovery procedure before deploying.

A missing or unready marker makes API queries bypass the cache. This does not
repair underlying data: reads during an update retain the existing uncached
behavior, without transaction-level snapshot isolation. Matching the stored tip
to the node does not prove historical address balances or UTXOs are correct.

`requirements-runtime.lock` constrains the existing runtime so application and
test builds resolve the same libraries. These are legacy dependencies with known
advisories; pinning them is not a security fix. Dependency/runtime modernization
and advisory triage remain separate work. Passing tests or `pip check` does not
establish production readiness or security clearance.

## Run the tests

Run the following Bash commands from the repository root with Docker available.
They build the application image, run tests against MongoDB 6.0.13, and exercise
the unmodified application image over HTTP with synthetic data and an RPC stub.

The tests accept only the named test MongoDB host and generate disposable
database names. Do not substitute production connection strings or real data.
Use a clean set of the names below; the preflight refuses existing containers.
No host ports are exposed.

```bash
set -euo pipefail
for name in api-cache-test-mongo api-cache-test-rpc api-cache-smoke-local; do
  if docker container inspect "$name" >/dev/null 2>&1; then
    echo "Test container already exists: $name" >&2
    exit 1
  fi
done
docker network create api-cache-test-network
cleanup() {
  docker rm -f api-cache-smoke-local api-cache-test-rpc api-cache-test-mongo >/dev/null 2>&1 || true
  docker network rm api-cache-test-network >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -t api-cache-candidate:local .
docker run --rm --network none --entrypoint python api-cache-candidate:local -m pip check
docker build --build-arg PYTHON_VERSION=3.8 -f tests/Dockerfile -t api-cache-tests:local .
docker run --detach --rm --name api-cache-test-mongo \
  --network api-cache-test-network mongo:6.0.13 --bind_ip_all --quiet
docker run --rm --network api-cache-test-network \
  -e TEST_MONGODB_URI=mongodb://api-cache-test-mongo:27017 \
  -e COVERAGE_FILE=/tmp/cache.coverage \
  --mount "type=bind,source=$PWD,target=/work,readonly" \
  api-cache-tests:local tests -q --cov=db.query_cache --cov-branch \
  --cov-report=term-missing --cov-fail-under=80

docker run --detach --rm --name api-cache-test-rpc \
  --network api-cache-test-network \
  --mount "type=bind,source=$PWD,target=/work,readonly" \
  --entrypoint python api-cache-tests:local /work/tests/fake_rpc.py
TEST_DATABASE="api_cache_http_${RANDOM}_$$"
docker run --detach --rm --name api-cache-smoke-local \
  --network api-cache-test-network \
  -e DB_URL=api-cache-test-mongo -e "DB_NAME=$TEST_DATABASE" \
  -e RPC_HOST=api-cache-test-rpc api-cache-candidate:local
docker run --rm --network api-cache-test-network \
  -e TEST_MONGODB_URI=mongodb://api-cache-test-mongo:27017 \
  -e "TEST_DATABASE=$TEST_DATABASE" -e TEST_API_URL=http://api-cache-smoke-local:5000 \
  --mount "type=bind,source=$PWD,target=/work,readonly" \
  --entrypoint python api-cache-tests:local /work/tests/http_smoke.py
```

Coverage is reported for `db.query_cache`, not the whole application. The suite
covers query identity, expiry, concurrent upserts, write errors, ingestion,
generation changes and reorg checks. The HTTP smoke expects 20 repeated requests
to cause two address aggregations, then verifies fresh data after a generation
change. Synthetic tests do not replace real-chain integration or recovery drills.

The pull-request workflow runs these checks and uploads JUnit/coverage reports
and a candidate-image archive identified by source SHA, retained for seven days.
The workflow has read-only repository permissions and does not publish images
to a registry or deploy them. The existing release workflow is unchanged.

## Deployment prerequisites

1. Review and validate the API and daemon together on an isolated database.
   Exercise interrupted ingestion, reorg detection and the recovery procedure.
2. Verify existing blocks, UTXOs and balances against a canonical checkpoint.
   Neither a matching tip hash nor a successful subsequent update proves the
   consistency of historical data.
3. Confirm a backup and application-data-only recovery path. Do not restore
   `cache_state` or `query_cache_v2` from a backup.
4. Deploy the daemon before the API. A database without a ready marker bypasses
   caching until a complete new ingestion has published a ready generation.
5. Verify canonical tip, ready state and repeated-request behavior. Compare
   HTTP errors, worker timeouts, slow queries and database load with baseline.
   Leave legacy-cache deletion outside this rollout.

## Recovery and rollback

After an interrupted update or detected reorg, stop the affected daemon and
preserve diagnostic evidence and a backup. Verify or rebuild application data
from a canonical source in isolation. Do not reset the marker just to restart
ingestion, or assume that bypassing the cache makes inconsistent data safe.

Only after data consistency has been independently verified, with the daemon
still stopped, may the operator remove the `_id: "blockchain"` marker from
`cache_state`. Restart and verify a complete update publishes a fresh ready
generation. This is a prerequisite, not an automated data-recovery implementation.

Roll back the API first, confirm all API instances use the previous image, then
roll back the daemon. **Do not run the new API with the old daemon:** the old
daemon does not invalidate generations and could leave cached results stale.
The previous code ignores the new collections but also lacks these safety
guards. Do not roll back merely to bypass a known data-integrity failure.
