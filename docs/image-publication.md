# Image publication

The Release workflow publishes the exact triggering `master` commit. Checkout
uses the job's automatic, read-only `GITHUB_TOKEN` without persisting Git
credentials. No personal GitHub token or GitHub App is required for this path.
Repository branch protection remains unchanged.

`Dockerfile.release` preserves the previously qualified runtime by pinning its
base digest. It removes the old application directory and copies the complete
reviewed source, including deletions, rather than overlaying selected modules.
The image receives an OCI source-revision label. An isolated probe compares all
application files byte-for-byte with the checkout and imports the actual Flask,
MongoEngine, PyMongo and analyzer modules; offline regressions then run in the
same image. All checks must pass before publication.

The ordinary Dockerfile remains separate: its floating dependencies can produce
a successful build and `pip check` while runtime imports fail. It is not the
release recipe. The pinned runtime retains legacy Python/dependency/security
limitations and is not a dependency modernization. Upgrade it in a separately
qualified change. These checks are not a full HTTP, database, writer-recovery or
production acceptance test; qualify the actual published artifact before rollout.

Before merging a runtime-base change, check the registry lifecycle policy and
tag retention. The pinned base must remain available and excluded from expiry;
keep its source-SHA tag even after `latest` moves. A digest pin alone does not
prevent registry deletion. The current untagged-only cleanup policy retains a
tagged base, but a changed policy or manual deletion can still break builds.

## Authentication and registry

This change preserves the existing AWS credential and role contract:
`CI_DEPLOYER_AWS_ACCESS_KEY_ID`, `CI_DEPLOYER_AWS_SECRET_ACCESS_KEY` and
`CI_DEPLOYER_AWS_ROLE_TO_ASSUME`. The destination remains the `api` repository
in account `401615164202`, region `eu-west-1`; the action verifies the account.
AWS/ECR authentication precedes the build because the pinned base is private.
The build uses no network for Dockerfile commands and receives no AWS build
arguments or secret mounts. No additional registry, IAM grant, OIDC provider or operator CLI credential is
introduced. AWS authentication failure stops publication. GitHub authentication
repair is not evidence that these separate AWS credentials are valid.

Docker credentials use a temporary runner directory shared by the ECR login,
build/test and publication steps. They are not mounted into build or test
containers. Action implementations are pinned to commit SHAs.
The publisher rejects a wrong target or source label, pushes the full source
SHA, and reads its digest back from the registry. It records that receipt before
pushing the backwards-compatible `latest` alias and verifying the same digest.

Publication is not atomic. If a push or manifest read fails, reconcile registry
state and the step summary before retrying; absence of a receipt does not prove
that no image was uploaded. There is no automatic deletion or rollback. Runs are
serialized without canceling an active push, but GitHub does not guarantee FIFO
ordering and replaces a pending run when a newer one arrives. Superseded pending
commits may therefore have no image. If that image is needed, rerun its workflow
after reconciling registry state and the effect on `latest`.
Retrying an old commit can move `latest` backwards. A SHA rebuild can
also differ because build metadata or tooling may differ even with a pinned base.
Deploy by the qualified registry digest, not by `latest`.

## Versioning and rollout

As in the separate ELCASH image-publication process, this workflow no longer
invokes automatic Python Semantic Release. It does not modify `setup.py` or
`CHANGELOG.md`, create Git tags/GitHub releases, or push version commits to
protected `master`. Semantic-version maintenance is a separate reviewed process;
the retained `setup.cfg` is not a validated automatic publishing path. Do not
restore it by copying a developer token into CI or weakening branch protection.

There is no deployment, migration, index creation, data repair or restart in
this workflow. Existing deployments pinned to other source tags or digests are
unchanged; any external consumers of `latest` must be considered separately.
Verify source, runtime/dependencies and regression behavior on the published
digest, then exercise API-only staging rollout/rollback before production.
An API image shared with a daemon must not accidentally roll both workloads.

## Local validation

With Python and PyYAML 6.0.2 installed:

```text
python -m unittest discover -s tests/workflows -v
```

The suite parses the workflow and mocks every Docker call, including failures
before and after upload. It needs no cloud credential, Docker daemon or database.
PR CI runs the same suite in a virtual environment separately from the ordinary
Dockerfile's application tests. It does not build the private release recipe or
exercise real dependency imports/source parity. Those checks run locally with
authorized registry access and in the release workflow before any push. PRs
must not receive AWS credentials just to pull the private base. A passing PR
check alone is not evidence that the release image can be built or started.
