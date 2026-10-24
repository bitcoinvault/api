#!/usr/bin/env bash
set -euo pipefail

: "${TEST_DATABASE:?Set a disposable api_cache_http_ fixture prefix}"
TEST_NETWORK="${TEST_NETWORK:-api-cache-ci}"
API_IMAGE="${API_IMAGE:-api-candidate}"
if [[ ! "$TEST_DATABASE" =~ ^api_cache_http_[A-Za-z0-9_]+$ ]] ||
   (( ${#TEST_DATABASE} + 18 > 63 )); then
  echo 'Recovery drill requires a short disposable fixture prefix' >&2
  exit 1
fi
if [[ ! "$TEST_NETWORK" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo 'Recovery drill requires a literal Docker network name' >&2
  exit 1
fi
if [[ ! "$API_IMAGE" =~ ^[A-Za-z0-9][A-Za-z0-9_.:/@-]*$ ]]; then
  echo 'Recovery drill requires a literal local image reference' >&2
  exit 1
fi
if [[ -n "${DOCKER_TLS_VERIFY:-}${DOCKER_CERT_PATH:-}" ]]; then
  echo 'Recovery drill requires a local Docker endpoint without TLS credentials' >&2
  exit 1
fi
docker_endpoint="${DOCKER_HOST:-}"
if [[ -n "${DOCKER_CONTEXT:-}" ]]; then
  docker_endpoint="$(docker context inspect "$DOCKER_CONTEXT" --format '{{.Endpoints.docker.Host}}')"
elif [[ -z "$docker_endpoint" ]]; then
  docker_endpoint="$(docker context inspect --format '{{.Endpoints.docker.Host}}')"
fi
case "$docker_endpoint" in
  unix:///*|npipe:////./pipe/*) ;;
  *) echo 'Recovery drill requires a local Docker socket or named pipe' >&2; exit 1 ;;
esac
if [[ "$(docker network inspect "$TEST_NETWORK" --format '{{.Internal}}')" != true ]]; then
  echo 'Recovery drill requires an internal Docker network' >&2
  exit 1
fi
for container in api-cache-test-mongo api-cache-test-rpc; do
  if [[ "$(docker inspect "$container" --format '{{.State.Running}}')" != true ]] ||
     [[ -z "$(docker inspect "$container" --format "{{with index .NetworkSettings.Networks \"$TEST_NETWORK\"}}{{.NetworkID}}{{end}}")" ]] ||
     [[ "$(docker inspect "$container" --format '{{len .NetworkSettings.Networks}}')" != 1 ]] ||
     [[ "$(docker inspect "$container" --format '{{.HostConfig.PublishAllPorts}}')" != false ]] ||
     [[ "$(docker inspect "$container" --format '{{len .HostConfig.PortBindings}}')" != 0 ]]; then
    echo 'Fixture containers must use only the isolated network and publish no ports' >&2
    exit 1
  fi
done
if ! docker image inspect "$API_IMAGE" >/dev/null; then
  echo 'Recovery drill requires an existing local candidate image' >&2
  exit 1
fi

checks_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if command -v cygpath >/dev/null 2>&1; then
  checks_dir="$(cygpath -w "$checks_dir")"
  export MSYS_NO_PATHCONV=1
fi
source_db="${TEST_DATABASE}_recovery_source"
restored_db="${TEST_DATABASE}_recovery_restored"
archive="/tmp/${TEST_DATABASE}_recovery.archive.gz"
docker exec api-cache-test-mongo test ! -e "$archive"
run_phase() {
  docker run --rm --pull=never --network "$TEST_NETWORK" \
    -e DB_PROTOCOL=mongodb -e DB_URL=api-cache-test-mongo -e DB_PORT=27017 \
    -e "DB_NAME=$source_db" -e DB_USERNAME= -e DB_PASSWORD= -e DB_URI_PARAMS= \
    -e RPC_HOST=api-cache-test-rpc -e RPC_PORT=8332 -e RPC_USER= -e RPC_PASSWORD= \
    -e HTTP_PROXY= -e HTTPS_PROXY= -e ALL_PROXY= \
    -e http_proxy= -e https_proxy= -e all_proxy= \
    --mount "type=bind,source=$checks_dir,target=/checks,readonly" \
    --entrypoint python "$API_IMAGE" /checks/recovery_smoke.py "$1"
}

run_phase prepare
docker exec api-cache-test-mongo mongodump --host=127.0.0.1 --port=27017 \
  --db="$source_db" --numParallelCollections=1 --excludeCollection=cache_state \
  --excludeCollection=query_cache_v2 --excludeCollection=request_cache \
  --archive="$archive" --gzip
docker exec api-cache-test-mongo mongorestore --host=127.0.0.1 --port=27017 \
  --archive="$archive" --gzip --nsInclude="$source_db.*" \
  --nsFrom="$source_db.*" --nsTo="$restored_db.*" --stopOnError
run_phase exercise
docker exec api-cache-test-mongo rm -- "$archive"
