# Releasing Zabt container images

The manual **Release** workflow builds selected images to unique GHCR staging tags, promotes them only after every selected build succeeds, and then creates the annotated Git tag and GitHub Release.

## Quick path

1. Merge the desired changes into `main`.
2. Open **Actions → Release → Run workflow**, select `main`, and choose the release inputs. For the
   first cloud-prod deployment, use `version=v1.0.0` and set `include_optional=true` so the required
   vision image is published.
3. Wait for every selected image build and promotion to pass. The workflow creates the Git tag and GitHub Release only after all image promotions succeed.

The workflow is manual-only. It has no `push` or `release` trigger, so publishing `latest` cannot start another release.

## Version inputs

| Input | Meaning |
| --- | --- |
| `release_type` | Required `patch`, `minor`, or `major` increment. |
| `version` | Optional exact `X.Y.Z` or `vX.Y.Z` override. The output is always normalized to `X.Y.Z` and the Git tag is `vX.Y.Z`. |
| `include_optional` | When `true`, also build and publish the vision and bot worker images. It defaults to `false`. |

When `version` is empty, the workflow finds the highest existing tag matching `vMAJOR.MINOR.PATCH`, uses `v0.0.0` as the starting point when no such tag exists, and applies the selected increment:

- **patch**: bug fixes and backward-compatible corrections (`1.2.3` → `1.2.4`).
- **minor**: backward-compatible features (`1.2.3` → `1.3.0`).
- **major**: breaking changes (`1.2.3` → `2.0.0`).

The version is validated before any image build. A new run is rejected if the Git tag or any selected GHCR image already has that version; only a retry of the same run can resume matching staged digests and a Git tag that points to the same commit. Dispatches from a branch other than `main` are also rejected.

Cloud production requires the optional vision image. Always choose `include_optional=true` when
publishing a release intended for `docker-compose.cloud.prod.yml`.

## Published images and tags

The owner is taken from the repository at runtime and lowercased for GHCR. Core images are:

```text
ghcr.io/<owner>/zabt-ai-api
ghcr.io/<owner>/zabt-ai-worker
ghcr.io/<owner>/zabt-ai-web
ghcr.io/<owner>/zabt-ai-gpu-worker
ghcr.io/<owner>/zabt-ai-cpu-worker
```

With `include_optional=true`, the workflow also publishes:

```text
ghcr.io/<owner>/zabt-ai-vision-worker
ghcr.io/<owner>/zabt-ai-bot-worker
```

Each selected image is first pushed under a staging tag derived from its semantic version and GitHub run ID. Only after all selected builds succeed does the promotion matrix add the canonical tags:

```text
ghcr.io/<owner>/zabt-ai-api:v1.2.3-run-123456789
ghcr.io/<owner>/zabt-ai-api:v1.2.3
ghcr.io/<owner>/zabt-ai-api:latest
```

Replace `zabt-ai-api` with the desired image name. The version tag is immutable for this workflow; use it for deployments and rollback. `latest` is mutable and is updated by promotion after all builds succeed; a failed promotion matrix can leave it updated for only some images.

Images include OCI source, revision, and normalized version labels. Buildx also publishes maximum provenance and an SBOM for each image.

## Permissions and first-time GHCR setup

The workflow uses the built-in `GITHUB_TOKEN` only:

- Preflight has `contents: read` and `packages: read` to inspect GHCR tags.
- Build and promotion jobs have `contents: read` and `packages: write`.
- The final publication job has `contents: write` to push the annotated tag and create the release.
- No registry password or hardcoded repository owner is stored in the workflow.

On the first run, check the package page under the repository or organization **Packages** area. GHCR packages commonly start private, subject to organization policy. Set the intended package visibility and repository association there before sharing an image. Public packages can be pulled anonymously; private packages require an authenticated user or automation token with package read access. Repository or organization policies may also require an administrator to allow GitHub Actions package publishing.

Preflight treats only explicit GHCR `MANIFEST_UNKNOWN` or `NAME_UNKNOWN` 404 responses as absent. Authentication, network, malformed-response, and other registry errors stop the release.

The cloud-prod Compose overlay pins all five required app images to one tag with
`ZABT_IMAGE_TAG=v1.0.0` (API, worker/beat, web, GPU worker, and vision worker) under
`ghcr.io/danielrondongarcia/`. The host must be able to pull each package: make them public for
anonymous access or log in with a credential that has `read:packages`.

Do not paste a token into this repository. For local access to a private package, use a temporary environment variable containing a token with the minimum required scope, such as `read:packages`:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
```

## Failure recovery

- **Build failure:** no semantic version or `latest` tags have been promoted. Successful matrix jobs keep their run-specific staging manifests. A same-run retry can reuse those records, but it fails closed if any per-image staging artifact is missing.
- **Missing staging artifact on retry:** do not rerun or rebuild the same workflow version. Check every selected GHCR package for the canonical semantic version tag. Start a fresh run with a new, unused version only after confirming none were created and the new run's preflight passes. If any canonical version tag exists, resolve the partial release without deleting, retagging, or overwriting it.
- **Promotion failure:** some images may already have their canonical tags. Rerun the failed jobs in the same workflow run. The workflow saved the pre-promotion `latest` digests in a 90-day run artifact; it skips a target only when its digest matches the staged manifest, promotes from the recorded baseline, and fails closed on any other digest or registry error.
- **Tag or release failure:** rerun the same workflow run. An existing Git tag is accepted only when it resolves to that run's commit; an existing GitHub Release is accepted only when it is complete for that tag.
- **New release run:** preflight checks every selected GHCR package before allowing a version. Reuse a version only when all selected packages lack that tag; if any package already has it, resume the original workflow run or investigate and select a new version. Do not delete or retag a partially promoted semantic version to clear the check.

GHCR does not provide an atomic transaction across image packages or tags. If promotion fails, a subset of canonical tags can temporarily point to the new release. The final Git tag and GitHub Release remain blocked until every promotion job succeeds. Authentication and network errors during preflight are failures, not evidence that a tag is absent.

## Pull and use the versioned CPU image

Use the immutable CPU worker tag rather than `latest` when deploying a known release:

```bash
docker pull ghcr.io/<owner>/zabt-ai-cpu-worker:v1.2.3
docker run --rm --name zabt-cpu-worker \
  -p 8001:8001 \
  ghcr.io/<owner>/zabt-ai-cpu-worker:v1.2.3
```

The worker still needs the runtime configuration required by the deployment, including access to its model provider and any application services it calls. In a Compose deployment, replace the worker service's `build` configuration with the versioned `image` reference and keep the existing environment and network settings.

## Optional images and build tradeoffs

The core release never builds the optional images unless `include_optional` is enabled. This keeps normal releases independent of optional profiles. If the input is enabled, both optional image jobs become part of the selected release and a failure blocks the tag and GitHub Release.

- **Vision worker**: the checked-in image is a CPU-oriented local-dev profile with the scene extra; it does not bake in the GPU Qwen3-VL stack.
- **Bot worker**: the image installs Chromium and uses an Ubuntu PPA for FFmpeg. It is larger and more dependent on external package availability, so builds can take longer or fail when the PPA is unavailable.

Enable the option only when those images are needed and their build dependencies are reachable from GitHub-hosted runners.

## Cloud-prod deployment

Run these commands from the repository root, the directory containing `docker-compose.yml`. Set
`ZABT_IMAGE_TAG` to a published immutable `vMAJOR.MINOR.PATCH` release such as `v1.0.0`; never use
`latest`. Compose requires a non-empty tag but cannot validate its format, so confirm it is an
existing release before deployment. The Docker workflow validates the tag format.

Docker Compose v2.24.4 or newer is required for the production overlay's `!override` and `!reset`
merge tags. Pull before starting without building locally:

```bash
export COMPOSE_PROFILES=cloud-prod
export ZABT_IMAGE_TAG=v1.0.0
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml up -d --no-build
```

## Rollback

For **cloud-prod**, restore the single tag and run both commands from the repository root:

```bash
export COMPOSE_PROFILES=cloud-prod
export ZABT_IMAGE_TAG=v1.2.2
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.cloud.prod.yml up -d --no-build
```

For a standalone CPU worker, use its previous exact tag, such as `ghcr.io/<owner>/zabt-ai-cpu-worker:v1.2.2`, then restart it using the deployment's normal procedure. Do not delete or retag previous immutable images.

New release runs reject an existing version tag, so a later run cannot silently replace `v1.2.2`. The `latest` tag may point at a partially promoted release, but deployments pinned to `v1.2.2` remain unaffected.
