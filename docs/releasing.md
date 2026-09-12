# Releasing Zabt container images

The manual **Release** workflow publishes an immutable semantic-versioned release to GitHub Container Registry (GHCR), updates the `latest` tags, creates an annotated Git tag, and creates a GitHub Release with automatically generated notes from commits and pull requests.

## Quick path

1. Merge the desired changes into `main`.
2. Open **Actions → Release → Run workflow**, select `main`, and choose the release inputs.
3. Wait for all selected image builds to pass. The workflow creates the tag and GitHub Release only after the builds succeed.

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

The version is validated before any image build. An existing `vX.Y.Z` tag is rejected; releases never overwrite an existing tag. Dispatches from a branch other than `main` are also rejected.

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

Every selected image receives both tags:

```text
ghcr.io/<owner>/zabt-ai-api:v1.2.3
ghcr.io/<owner>/zabt-ai-api:latest
```

Replace `zabt-ai-api` with the desired image name. The version tag is immutable for this workflow; use it for deployments and rollback. `latest` is updated only by this manual release workflow and is intended for development or explicitly floating deployments.

Images include OCI source, revision, and normalized version labels. Buildx also publishes maximum provenance and an SBOM for each image.

## Permissions and first-time GHCR setup

The workflow uses the built-in `GITHUB_TOKEN` only:

- Image jobs have `contents: read` and `packages: write`.
- The final publication job has `contents: write` to push the annotated tag and create the release.
- No registry password or hardcoded repository owner is stored in the workflow.

On the first run, check the package page under the repository or organization **Packages** area. GHCR packages commonly start private, subject to organization policy. Set the intended package visibility and repository association there before sharing an image. Public packages can be pulled anonymously; private packages require an authenticated user or automation token with package read access. Repository or organization policies may also require an administrator to allow GitHub Actions package publishing.

Do not paste a token into this repository. For local access to a private package, use a temporary environment variable containing a token with the minimum required scope, such as `read:packages`:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
```

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

## Rollback

1. Identify the last known-good release, for example `v1.2.2`.
2. Change each deployed image reference from `:latest` or the failed version to the exact previous tag, such as `ghcr.io/<owner>/zabt-ai-cpu-worker:v1.2.2`.
3. Pull the pinned images and restart the deployment using its normal operational procedure.
4. Investigate the failed release before attempting another version. Do not delete or retag the previous immutable images.

Because the workflow rejects an existing version tag, a later release cannot silently replace `v1.2.2`. The `latest` tag may point at the failed release, but deployments pinned to `v1.2.2` remain unaffected.
