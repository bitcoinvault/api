#!/usr/bin/env bash
set -euo pipefail

# Keep the CI runtime dependencies, not its copy of the PR's application.
base_dir=$(mktemp -d)
trap 'rm -rf -- "$base_dir"' EXIT
git archive "$1" src | tar -x -C "$base_dir"
docker build --pull=false --network none -t btcv-pr-recipe-base -f - "$base_dir" <<'DOCKERFILE'
FROM bitcoin-vault-api-ci
RUN rm -rf /app
WORKDIR /app
COPY src .
RUN rm -f blockchain/rpc.py && python -B -c "from importlib.util import find_spec; assert find_spec('blockchain.rpc') is None"
DOCKERFILE

for recipe in Dockerfile.release Dockerfile.io-relief; do
    # Replace only the single pinned FROM. Run the checked-in COPY and RUN steps.
    from=$(grep -i '^FROM ' "$recipe")
    [[ "$from" =~ ^FROM\ [^[:space:]]+@sha256:[0-9a-f]{64}$ ]]
    image="btcv-pr-${recipe#Dockerfile.}"
    sed 's/^FROM .*/FROM btcv-pr-recipe-base/' "$recipe" |
        docker build --pull=false --network none -t "$image" -f - .
    docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
        --pids-limit 128 --memory 256m --cpus 1 --entrypoint python \
        --mount "type=bind,source=$PWD/tests,target=/tests,readonly" "$image" \
        -B -m unittest discover -s /tests -p test_vault_journal.py -v
done
