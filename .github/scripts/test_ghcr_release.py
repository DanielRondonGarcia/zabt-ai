import json
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from ghcr_release import (
    GHCRClient,
    HttpResponse,
    Manifest,
    RegistryError,
    preflight,
    promote_image,
    record_staging_digest,
    verify_staging_digest,
)


OWNER = "danielrondongarcia"
IMAGES = ["zabt-ai-api", "zabt-ai-worker"]
MATRIX = {"include": [{"image": image} for image in IMAGES]}
VERSION_TAG = "v1.0.0"
STAGING_TAG = "v1.0.0-run-12345"
RUN_ID = "12345"
COMMIT = "0123456789abcdef"
SOURCE_REPOSITORY = "danielrondongarcia/zabt-ai"
MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"


def manifest(digest: str) -> Manifest:
    return Manifest(digest, MEDIA_TYPE, digest.encode())


def valid_manifest(label: str) -> Manifest:
    body = json.dumps(
        {"schemaVersion": 2, "mediaType": MEDIA_TYPE, "payload": label},
        separators=(",", ":"),
    ).encode()
    return Manifest(f"sha256:{hashlib.sha256(body).hexdigest()}", MEDIA_TYPE, body)


class FakeRegistry:
    def __init__(self):
        self.tags = {}
        self.put_count = 0
        self.fail_after_put = False
        self.after_put = None

    def get_manifest(self, repository: str, reference: str):
        return self.tags.get((repository, reference))

    def put_manifest(self, repository: str, reference: str, value: Manifest):
        self.put_count += 1
        self.tags[(repository, reference)] = value
        if self.after_put is not None:
            self.after_put(repository, reference, value)
        if self.fail_after_put:
            self.fail_after_put = False
            raise RegistryError("simulated lost response after manifest PUT")
        return HttpResponse(201, {}, b"")


class GHCRReleaseTests(unittest.TestCase):
    def write_staging_record(self, registry, image, directory):
        record_file = Path(directory) / "ghcr-stage.json"
        record_staging_digest(
            registry,
            owner=OWNER,
            repository=SOURCE_REPOSITORY,
            image=image,
            staging_tag=STAGING_TAG,
            run_id=RUN_ID,
            commit=COMMIT,
            expected_digest=registry.tags[(f"{OWNER}/{image}", STAGING_TAG)].digest,
            record_file=str(record_file),
        )
        return record_file

    def test_release_workflow_keeps_seven_core_and_two_optional_images(self):
        workflow = Path(__file__).parents[1] / "workflows" / "release.yml"
        source = workflow.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^[ \t]+matrix=\"\$\(python3 - <<'PY'\n(.*?)^[ \t]+PY\n[ \t]+\)\"",
            source,
        )
        self.assertIsNotNone(match, "Release workflow image-matrix generator was not found")
        matrix_script = textwrap.dedent(match.group(1))

        def selected_matrix(include_optional: str) -> list[dict[str, str]]:
            environment = os.environ.copy()
            environment["INCLUDE_OPTIONAL"] = include_optional
            result = subprocess.run(
                [sys.executable, "-c", matrix_script],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            return json.loads(result.stdout)["include"]

        def selected_images(include_optional: str) -> set[str]:
            return {image["image"] for image in selected_matrix(include_optional)}

        core = {
            "zabt-ai-api",
            "zabt-ai-worker",
            "zabt-ai-web",
            "zabt-ai-gpu-worker",
            "zabt-ai-cpu-worker",
            "zabt-ai-minio",
            "zabt-ai-mc",
        }
        self.assertEqual(selected_images("false"), core)
        self.assertEqual(
            selected_images("true"),
            core | {"zabt-ai-vision-worker", "zabt-ai-bot-worker"},
        )

        core_matrix = {image["image"]: image for image in selected_matrix("false")}
        for image, dockerfile in (
            ("zabt-ai-minio", "docker/minio/Dockerfile"),
            ("zabt-ai-mc", "docker/minio/Dockerfile.mc"),
        ):
            with self.subTest(image=image):
                self.assertEqual(core_matrix[image]["context"], "docker/minio")
                self.assertEqual(core_matrix[image]["file"], dockerfile)

        notes_start = source.index('echo "## Published container images"')
        notes_end = source.index('} > "$notes_file"', notes_start)
        notes_script = source[notes_start:notes_end]
        self.assertIn("zabt-ai-minio", notes_script)
        self.assertIn("zabt-ai-mc", notes_script)

    def test_release_workflow_binds_stage_record_to_buildx_digest_output(self):
        workflow = Path(__file__).parents[1] / "workflows" / "release.yml"
        source = workflow.read_text(encoding="utf-8")

        self.assertRegex(
            source,
            r"(?ms)- name: Build and push image\s+id: build\s+if: steps\.stage-plan\.outputs\.build_required == 'true'\s+uses: docker/build-push-action@v6",
        )
        self.assertIn('BUILDX_DIGEST: "${{ steps.build.outputs.digest }}"', source)
        self.assertIn('--expected-digest "$BUILDX_DIGEST"', source)
        self.assertIn("build_required=false", source)
        self.assertIn("verify-stage", source)

    def test_retry_without_staging_artifact_fails_before_build_path(self):
        workflow = Path(__file__).parents[1] / "workflows" / "release.yml"
        source = workflow.read_text(encoding="utf-8")

        for step_name in (
            "Restore this image's staging digest on retry",
            "Restore this image's immutable staging digest",
        ):
            start = source.index(f"      - name: {step_name}")
            end = source.find("\n      - name:", start + 1)
            step = source[start:end if end >= 0 else len(source)]
            self.assertIn("uses: actions/download-artifact@v4", step)
            self.assertIn("name: ghcr-stage-${{ github.run_id }}-${{ matrix.image }}", step)
            self.assertNotIn("continue-on-error:", step)

        plan_start = source.index("      - name: Plan build or verify the recorded staging manifest")
        plan_end = source.index("\n      - name:", plan_start + 1)
        plan_step = source[plan_start:plan_end]
        script_start = plan_step.index("        run: |\n") + len("        run: |\n")
        plan_script = textwrap.dedent(plan_step[script_start:])

        with tempfile.TemporaryDirectory() as temp_dir:
            output_file = Path(temp_dir) / "github-output"
            environment = os.environ.copy()
            environment.update(
                {
                    "RUNNER_TEMP": temp_dir,
                    "RUN_ATTEMPT": "2",
                    "GITHUB_OUTPUT": str(output_file),
                }
            )
            result = subprocess.run(
                ["bash", "-c", plan_script],
                cwd=Path(__file__).parents[2],
                env=environment,
                capture_output=True,
                text=True,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staging digest artifact is missing", result.stdout + result.stderr)
        self.assertFalse(output_file.exists(), "retry must not emit build_required=true without its artifact")

    def test_manifest_unknown_is_the_only_accepted_absence_response(self):
        client = GHCRClient("ci", "token", opener=lambda *_args, **_kwargs: None)
        with patch.object(client, "_bearer_token", return_value="bearer"):
            with patch.object(
                client,
                "_send",
                return_value=HttpResponse(
                    404,
                    {},
                    b'{"errors":[{"code":"MANIFEST_UNKNOWN"}]}',
                ),
            ):
                self.assertIsNone(client.get_manifest(f"{OWNER}/{IMAGES[0]}", VERSION_TAG))

            for response in (
                HttpResponse(401, {}, b'{"errors":[{"code":"UNAUTHORIZED"}]}'),
                HttpResponse(403, {}, b'{"errors":[{"code":"DENIED"}]}'),
                HttpResponse(404, {}, b'{"errors":[{"code":"DENIED"}]}'),
                HttpResponse(404, {}, b"not a registry error response"),
            ):
                with self.subTest(status=response.status, body=response.body):
                    with patch.object(client, "_send", return_value=response):
                        with self.assertRaises(RegistryError):
                            client.get_manifest(f"{OWNER}/{IMAGES[0]}", VERSION_TAG)

            with patch.object(client, "_send", side_effect=RegistryError("network unavailable")):
                with self.assertRaisesRegex(RegistryError, "network unavailable"):
                    client.get_manifest(f"{OWNER}/{IMAGES[0]}", VERSION_TAG)

            with patch.object(
                client,
                "_send",
                return_value=HttpResponse(404, {}, b'{"errors":[{"code":"NAME_UNKNOWN"}]}'),
            ):
                self.assertIsNone(client.get_manifest(f"{OWNER}/{IMAGES[0]}", VERSION_TAG))

    def test_manifest_digest_is_computed_and_checked_against_registry_header(self):
        client = GHCRClient("ci", "token", opener=lambda *_args, **_kwargs: None)
        body = b'{"mediaType":"application/vnd.oci.image.manifest.v1+json"}'
        digest = f"sha256:{hashlib.sha256(body).hexdigest()}"
        with patch.object(client, "_bearer_token", return_value="bearer"):
            with patch.object(
                client,
                "_send",
                return_value=HttpResponse(
                    200,
                    {
                        "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                        "Docker-Content-Digest": digest,
                    },
                    body,
                ),
            ):
                self.assertEqual(
                    client.get_manifest(f"{OWNER}/{IMAGES[0]}", VERSION_TAG).digest,
                    digest,
                )

            with patch.object(
                client,
                "_send",
                return_value=HttpResponse(
                    200,
                    {
                        "Content-Type": "application/vnd.oci.image.manifest.v1+json",
                        "Docker-Content-Digest": "sha256:wrong",
                    },
                    body,
                ),
            ):
                with self.assertRaisesRegex(RegistryError, "digest mismatch"):
                    client.get_manifest(f"{OWNER}/{IMAGES[0]}", VERSION_TAG)

    def test_preflight_captures_latest_baseline_and_rejects_existing_version(self):
        registry = FakeRegistry()
        for image in IMAGES:
            registry.tags[(f"{OWNER}/{image}", "latest")] = manifest(f"sha256:old-{image}")

        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_path = Path(temp_dir) / "baseline.json"
            prepared = preflight(
                registry,
                owner=OWNER,
                version_tag=VERSION_TAG,
                staging_tag=STAGING_TAG,
                matrix=MATRIX,
                run_id=RUN_ID,
                attempt=1,
                commit=COMMIT,
                baseline_file=str(baseline_path),
            )
            self.assertEqual(
                [entry["baseline_latest_digest"] for entry in prepared["include"]],
                [f"sha256:old-{image}" for image in IMAGES],
            )
            self.assertEqual(json.loads(baseline_path.read_text())["run_id"], RUN_ID)

        registry.tags[(f"{OWNER}/{IMAGES[1]}", VERSION_TAG)] = manifest("sha256:stale")
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RegistryError, "already exists"):
                preflight(
                    registry,
                    owner=OWNER,
                    version_tag=VERSION_TAG,
                    staging_tag=STAGING_TAG,
                    matrix=MATRIX,
                    run_id=RUN_ID,
                    attempt=1,
                    commit=COMMIT,
                    baseline_file=str(Path(temp_dir) / "baseline.json"),
                )

    def test_new_preflight_rejects_a_preexisting_run_staging_tag(self):
        registry = FakeRegistry()
        repository = f"{OWNER}/{IMAGES[0]}"
        registry.tags[(repository, STAGING_TAG)] = manifest("sha256:unexpected-stage")

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RegistryError, "staging tag .* already exists"):
                preflight(
                    registry,
                    owner=OWNER,
                    version_tag=VERSION_TAG,
                    staging_tag=STAGING_TAG,
                    matrix=MATRIX,
                    run_id=RUN_ID,
                    attempt=1,
                    commit=COMMIT,
                    baseline_file=str(Path(temp_dir) / "baseline.json"),
                )

    def test_retry_preflight_allows_only_matching_staged_version_tags(self):
        registry = FakeRegistry()
        baseline = {
            "schema": 1,
            "owner": OWNER,
            "version_tag": VERSION_TAG,
            "staging_tag": STAGING_TAG,
            "run_id": RUN_ID,
            "commit": COMMIT,
            "images": sorted(IMAGES),
            "latest_baseline": {image: None for image in IMAGES},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            baseline_path = Path(temp_dir) / "baseline.json"
            baseline_path.write_text(json.dumps(baseline))
            repository = f"{OWNER}/{IMAGES[0]}"
            staged = manifest("sha256:staged")
            registry.tags[(repository, STAGING_TAG)] = staged
            registry.tags[(repository, VERSION_TAG)] = staged
            registry.tags[(f"{OWNER}/{IMAGES[1]}", STAGING_TAG)] = manifest(
                "sha256:staged-worker"
            )

            prepared = preflight(
                registry,
                owner=OWNER,
                version_tag=VERSION_TAG,
                staging_tag=STAGING_TAG,
                matrix=MATRIX,
                run_id=RUN_ID,
                attempt=2,
                commit=COMMIT,
                baseline_file=str(baseline_path),
            )
            self.assertEqual(prepared["include"][0]["baseline_latest_digest"], "")

            registry.tags[(repository, VERSION_TAG)] = manifest("sha256:wrong")
            with self.assertRaisesRegex(RegistryError, "do not match"):
                preflight(
                    registry,
                    owner=OWNER,
                    version_tag=VERSION_TAG,
                    staging_tag=STAGING_TAG,
                    matrix=MATRIX,
                    run_id=RUN_ID,
                    attempt=2,
                    commit=COMMIT,
                    baseline_file=str(baseline_path),
                )

    def test_promote_updates_latest_then_version_from_recorded_baseline(self):
        registry = FakeRegistry()
        repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("new")
        registry.tags[(repository, STAGING_TAG)] = staged
        registry.tags[(repository, "latest")] = manifest("sha256:old")

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            promote_image(
                registry,
                owner=OWNER,
                repository=SOURCE_REPOSITORY,
                image=IMAGES[0],
                staging_tag=STAGING_TAG,
                version_tag=VERSION_TAG,
                run_id=RUN_ID,
                commit=COMMIT,
                record_file=str(record_file),
                baseline_latest_digest="sha256:old",
            )

        self.assertEqual(registry.tags[(repository, "latest")].digest, staged.digest)
        self.assertEqual(registry.tags[(repository, VERSION_TAG)].digest, staged.digest)

    def test_promotion_retry_skips_matching_targets_by_digest(self):
        registry = FakeRegistry()
        repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("new")
        registry.tags[(repository, STAGING_TAG)] = staged
        registry.tags[(repository, "latest")] = staged
        registry.tags[(repository, VERSION_TAG)] = staged

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            promote_image(
                registry,
                owner=OWNER,
                repository=SOURCE_REPOSITORY,
                image=IMAGES[0],
                staging_tag=STAGING_TAG,
                version_tag=VERSION_TAG,
                run_id=RUN_ID,
                commit=COMMIT,
                record_file=str(record_file),
                baseline_latest_digest="sha256:old",
            )

        self.assertEqual(registry.put_count, 0)

    def test_promotion_fails_closed_when_latest_changed_after_preflight(self):
        registry = FakeRegistry()
        repository = f"{OWNER}/{IMAGES[0]}"
        registry.tags[(repository, STAGING_TAG)] = valid_manifest("new")
        registry.tags[(repository, "latest")] = manifest("sha256:unexpected")

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            with self.assertRaisesRegex(RegistryError, "changed after preflight"):
                promote_image(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    version_tag=VERSION_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    record_file=str(record_file),
                    baseline_latest_digest="sha256:old",
                )

    def test_uncertain_put_is_accepted_only_after_exact_digest_verification(self):
        registry = FakeRegistry()
        repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("new")
        registry.tags[(repository, STAGING_TAG)] = staged
        registry.tags[(repository, "latest")] = manifest("sha256:old")
        registry.fail_after_put = True

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            promote_image(
                registry,
                owner=OWNER,
                repository=SOURCE_REPOSITORY,
                image=IMAGES[0],
                staging_tag=STAGING_TAG,
                version_tag=VERSION_TAG,
                run_id=RUN_ID,
                commit=COMMIT,
                record_file=str(record_file),
                baseline_latest_digest="sha256:old",
            )

        self.assertEqual(registry.tags[(repository, VERSION_TAG)].digest, staged.digest)

    def test_existing_version_mismatch_fails_without_overwriting(self):
        registry = FakeRegistry()
        repository = f"{OWNER}/{IMAGES[0]}"
        registry.tags[(repository, STAGING_TAG)] = valid_manifest("new")
        registry.tags[(repository, VERSION_TAG)] = manifest("sha256:other")

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            with self.assertRaisesRegex(RegistryError, "does not match its staged manifest"):
                promote_image(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    version_tag=VERSION_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    record_file=str(record_file),
                    baseline_latest_digest=None,
                )

    def test_staging_digest_record_is_valid_and_verified_against_registry(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("recorded")
        registry.tags[(registry_repository, STAGING_TAG)] = staged

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            record = json.loads(record_file.read_text(encoding="utf-8"))
            self.assertEqual(
                record,
                {
                    "schema": 1,
                    "owner": OWNER,
                    "repository": SOURCE_REPOSITORY,
                    "image": IMAGES[0],
                    "staging_tag": STAGING_TAG,
                    "run_id": RUN_ID,
                    "commit": COMMIT,
                    "digest": staged.digest,
                    "media_type": MEDIA_TYPE,
                },
            )
            self.assertEqual(
                verify_staging_digest(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    record_file=str(record_file),
                ),
                staged,
            )

    def test_staging_digest_record_rejects_missing_or_invalid_manifests(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        valid = valid_manifest("valid")
        invalid_manifests = (
            None,
            Manifest("sha256:invalid", MEDIA_TYPE, b"manifest"),
            Manifest(valid.digest, "application/vnd.invalid+json", valid.body),
            Manifest(
                f"sha256:{hashlib.sha256(b'not-json').hexdigest()}",
                MEDIA_TYPE,
                b"not-json",
            ),
        )

        for staged in invalid_manifests:
            with self.subTest(staged=staged), tempfile.TemporaryDirectory() as temp_dir:
                if staged is None:
                    registry.tags.pop((registry_repository, STAGING_TAG), None)
                else:
                    registry.tags[(registry_repository, STAGING_TAG)] = staged
                with self.assertRaises(RegistryError):
                    record_staging_digest(
                        registry,
                        owner=OWNER,
                        repository=SOURCE_REPOSITORY,
                        image=IMAGES[0],
                        staging_tag=STAGING_TAG,
                        run_id=RUN_ID,
                        commit=COMMIT,
                        expected_digest=valid.digest,
                        record_file=str(Path(temp_dir) / "ghcr-stage.json"),
                    )

    def test_staging_digest_record_rejects_mismatched_bindings(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        registry.tags[(registry_repository, STAGING_TAG)] = valid_manifest("recorded")

        for field, replacement in (
            ("run_id", "other-run"),
            ("commit", "fedcba9876543210"),
            ("image", IMAGES[1]),
            ("staging_tag", "v1.0.0-run-elsewhere"),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
                record = json.loads(record_file.read_text(encoding="utf-8"))
                record[field] = replacement
                record_file.write_text(json.dumps(record), encoding="utf-8")
                with self.assertRaisesRegex(RegistryError, f"({field})"):
                    verify_staging_digest(
                        registry,
                        owner=OWNER,
                        repository=SOURCE_REPOSITORY,
                        image=IMAGES[0],
                        staging_tag=STAGING_TAG,
                        run_id=RUN_ID,
                        commit=COMMIT,
                        record_file=str(record_file),
                    )

    def test_buildx_digest_mismatch_does_not_write_a_record_or_allow_promotion(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("live-staging-manifest")
        buildx_manifest = valid_manifest("buildx-output")
        registry.tags[(registry_repository, STAGING_TAG)] = staged

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = Path(temp_dir) / "ghcr-stage.json"
            with self.assertRaisesRegex(RegistryError, "but Buildx reported"):
                record_staging_digest(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    expected_digest=buildx_manifest.digest,
                    record_file=str(record_file),
                )

            self.assertFalse(record_file.exists())
            with self.assertRaisesRegex(RegistryError, "artifact is missing or invalid"):
                promote_image(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    version_tag=VERSION_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    record_file=str(record_file),
                    baseline_latest_digest=None,
                )

        self.assertEqual(registry.put_count, 0)

    def test_mutated_staging_digest_is_rejected_before_any_promotion(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        registry.tags[(registry_repository, STAGING_TAG)] = valid_manifest("original")

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            registry.tags[(registry_repository, STAGING_TAG)] = valid_manifest("mutated")
            with self.assertRaisesRegex(RegistryError, "differs from the recorded build digest"):
                promote_image(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    version_tag=VERSION_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    record_file=str(record_file),
                    baseline_latest_digest=None,
                )
        self.assertEqual(registry.put_count, 0)

    def test_successful_promotion_uses_the_recorded_build_digest(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("recorded-build")
        registry.tags[(registry_repository, STAGING_TAG)] = staged
        registry.tags[(registry_repository, "latest")] = manifest("sha256:baseline")

        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            record = json.loads(record_file.read_text(encoding="utf-8"))
            promote_image(
                registry,
                owner=OWNER,
                repository=SOURCE_REPOSITORY,
                image=IMAGES[0],
                staging_tag=STAGING_TAG,
                version_tag=VERSION_TAG,
                run_id=RUN_ID,
                commit=COMMIT,
                record_file=str(record_file),
                baseline_latest_digest="sha256:baseline",
            )

        self.assertEqual(registry.tags[(registry_repository, "latest")].digest, record["digest"])
        self.assertEqual(registry.tags[(registry_repository, VERSION_TAG)].digest, record["digest"])

    def test_staging_is_rechecked_before_each_target_put(self):
        registry = FakeRegistry()
        registry_repository = f"{OWNER}/{IMAGES[0]}"
        staged = valid_manifest("recorded-build")
        registry.tags[(registry_repository, STAGING_TAG)] = staged
        registry.tags[(registry_repository, "latest")] = manifest("sha256:baseline")

        def mutate_staging_after_latest_put(_repository, reference, _value):
            if reference == "latest":
                registry.tags[(registry_repository, STAGING_TAG)] = valid_manifest("mutated")

        registry.after_put = mutate_staging_after_latest_put
        with tempfile.TemporaryDirectory() as temp_dir:
            record_file = self.write_staging_record(registry, IMAGES[0], temp_dir)
            with self.assertRaisesRegex(RegistryError, "changed before promotion"):
                promote_image(
                    registry,
                    owner=OWNER,
                    repository=SOURCE_REPOSITORY,
                    image=IMAGES[0],
                    staging_tag=STAGING_TAG,
                    version_tag=VERSION_TAG,
                    run_id=RUN_ID,
                    commit=COMMIT,
                    record_file=str(record_file),
                    baseline_latest_digest="sha256:baseline",
                )

        self.assertEqual(registry.put_count, 1)
        self.assertEqual(registry.tags[(registry_repository, "latest")].digest, staged.digest)
        self.assertNotIn((registry_repository, VERSION_TAG), registry.tags)


if __name__ == "__main__":
    unittest.main()
