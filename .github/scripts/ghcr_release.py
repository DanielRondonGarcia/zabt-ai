"""Fail-closed GHCR manifest checks and promotion for the release workflow."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


GHCR = "https://ghcr.io"
TOKEN_ENDPOINT = f"{GHCR}/token"
MANIFEST_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
OWNER_PATTERN = re.compile(r"[a-z0-9-]+")
IMAGE_PATTERN = re.compile(r"zabt-ai-[a-z0-9-]+")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
SUPPORTED_MEDIA_TYPES = frozenset(MANIFEST_ACCEPT.split(", "))


class RegistryError(RuntimeError):
    """An authentication, network, or registry error that must fail closed."""


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class Manifest:
    digest: str
    media_type: str
    body: bytes


class GHCRClient:
    def __init__(
        self,
        username: str,
        password: str,
        *,
        opener=urlopen,
        timeout: int = 30,
    ) -> None:
        self.username = username
        self.password = password
        self.opener = opener
        self.timeout = timeout
        self._tokens: dict[tuple[str, str], str] = {}

    def _send(self, request: Request) -> HttpResponse:
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return HttpResponse(
                    status=int(response.status),
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except HTTPError as error:
            return HttpResponse(
                status=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(),
            )
        except (URLError, TimeoutError, OSError) as error:
            raise RegistryError(f"GHCR request failed: {error}") from error

    def _bearer_token(self, repository: str, actions: str) -> str:
        cache_key = (repository, actions)
        if cache_key in self._tokens:
            return self._tokens[cache_key]

        query = urlencode(
            {
                "service": "ghcr.io",
                "scope": f"repository:{repository}:{actions}",
            }
        )
        credentials = base64.b64encode(
            f"{self.username}:{self.password}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            f"{TOKEN_ENDPOINT}?{query}",
            headers={
                "Authorization": f"Basic {credentials}",
                "Accept": "application/json",
            },
            method="GET",
        )
        response = self._send(request)
        if response.status != 200:
            raise RegistryError(
                f"GHCR token request for {repository} failed with HTTP {response.status}."
            )
        try:
            payload = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RegistryError("GHCR returned an invalid token response.") from error
        token = payload.get("token") or payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RegistryError("GHCR token response did not contain a bearer token.")
        self._tokens[cache_key] = token
        return token

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str | None:
        for key, value in headers.items():
            if key.lower() == name.lower():
                return value
        return None

    @staticmethod
    def _registry_error_codes(body: bytes) -> set[str]:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return set()
        errors = payload.get("errors", []) if isinstance(payload, dict) else []
        return {
            entry.get("code")
            for entry in errors
            if isinstance(entry, dict) and isinstance(entry.get("code"), str)
        }

    def _manifest_request(
        self,
        method: str,
        repository: str,
        reference: str,
        *,
        actions: str = "pull",
        manifest: Manifest | None = None,
    ) -> HttpResponse:
        token = self._bearer_token(repository, actions)
        url = f"{GHCR}/v2/{quote(repository, safe='/')}/manifests/{quote(reference, safe='')}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": MANIFEST_ACCEPT,
        }
        body = None
        if manifest is not None:
            headers["Content-Type"] = manifest.media_type
            body = manifest.body
        return self._send(Request(url, data=body, headers=headers, method=method))

    def get_manifest(self, repository: str, reference: str) -> Manifest | None:
        response = self._manifest_request("GET", repository, reference)
        if response.status == 404:
            # Absence is accepted only for the registry's explicit missing
            # manifest/repository codes. Malformed responses and all other
            # errors remain failures; auth and network failures never become
            # an "unused" tag result.
            codes = self._registry_error_codes(response.body)
            if codes.intersection({"MANIFEST_UNKNOWN", "NAME_UNKNOWN"}):
                return None
            raise RegistryError(
                f"GHCR returned an unclassified 404 for {repository}:{reference}; refusing to treat it as absent."
            )
        if response.status != 200:
            raise RegistryError(
                f"GHCR manifest lookup for {repository}:{reference} failed with HTTP {response.status}."
            )
        if not response.body:
            raise RegistryError(f"GHCR returned an empty manifest for {repository}:{reference}.")

        computed_digest = f"sha256:{hashlib.sha256(response.body).hexdigest()}"
        reported_digest = self._header(response.headers, "Docker-Content-Digest")
        if reported_digest and reported_digest != computed_digest:
            raise RegistryError(
                f"GHCR returned a manifest digest mismatch for {repository}:{reference}."
            )
        content_type = self._header(response.headers, "Content-Type")
        media_type = content_type.split(";", 1)[0].strip() if content_type else ""
        if not media_type:
            try:
                media_type = json.loads(response.body).get("mediaType", "")
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                media_type = ""
        if not media_type:
            raise RegistryError(
                f"GHCR did not identify the manifest media type for {repository}:{reference}."
            )
        return Manifest(computed_digest, media_type, response.body)

    def put_manifest(
        self,
        repository: str,
        reference: str,
        manifest: Manifest,
    ) -> HttpResponse:
        return self._manifest_request(
            "PUT",
            repository,
            reference,
            actions="pull,push",
            manifest=manifest,
        )


def _validate_names(owner: str, images: list[dict[str, Any]]) -> list[str]:
    if not OWNER_PATTERN.fullmatch(owner):
        raise RegistryError("GHCR owner must be a lowercase registry namespace.")
    names = [item.get("image") for item in images]
    if not names or any(not isinstance(name, str) or not IMAGE_PATTERN.fullmatch(name) for name in names):
        raise RegistryError("Release matrix contains an invalid or empty GHCR image list.")
    if len(set(names)) != len(names):
        raise RegistryError("Release matrix contains duplicate GHCR images.")
    return names


def _read_matrix(matrix_json: str) -> dict[str, Any]:
    try:
        matrix = json.loads(matrix_json)
    except json.JSONDecodeError as error:
        raise RegistryError("Release image matrix is not valid JSON.") from error
    if not isinstance(matrix, dict) or not isinstance(matrix.get("include"), list):
        raise RegistryError("Release image matrix must contain an include list.")
    _validate_names("danielrondongarcia", matrix["include"])
    return matrix


def _baseline_payload(
    path: str,
    *,
    owner: str,
    version_tag: str,
    staging_tag: str,
    run_id: str,
    commit: str,
    images: list[str],
) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as baseline_file:
            payload = json.load(baseline_file)
    except (OSError, json.JSONDecodeError) as error:
        raise RegistryError("The original GHCR preflight artifact is missing or invalid.") from error
    if not isinstance(payload, dict):
        raise RegistryError("The original GHCR preflight artifact has an invalid structure.")
    expected = {
        "schema": 1,
        "owner": owner,
        "version_tag": version_tag,
        "staging_tag": staging_tag,
        "run_id": str(run_id),
        "commit": commit,
        "images": sorted(images),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RegistryError(f"GHCR preflight artifact does not match this release run ({key}).")
    baseline = payload.get("latest_baseline")
    if not isinstance(baseline, dict) or set(baseline) != set(images):
        raise RegistryError("GHCR preflight artifact has an incomplete latest-tag baseline.")
    if any(value is not None and not isinstance(value, str) for value in baseline.values()):
        raise RegistryError("GHCR preflight artifact contains an invalid manifest digest.")
    return payload


def preflight(
    client: GHCRClient,
    *,
    owner: str,
    version_tag: str,
    staging_tag: str,
    matrix: dict[str, Any],
    run_id: str,
    attempt: int,
    commit: str,
    baseline_file: str,
) -> dict[str, Any]:
    images = matrix["include"]
    names = _validate_names(owner, images)

    if attempt == 1:
        latest_baseline: dict[str, str | None] = {}
        for image in names:
            repository = f"{owner}/{image}"
            existing_stage = client.get_manifest(repository, staging_tag)
            if existing_stage is not None:
                raise RegistryError(
                    f"Run-specific staging tag {repository}:{staging_tag} already exists before this run."
                )
            existing = client.get_manifest(repository, version_tag)
            if existing is not None:
                raise RegistryError(
                    f"GHCR version tag {repository}:{version_tag} already exists; choose a new version."
                )
            latest = client.get_manifest(repository, "latest")
            latest_baseline[image] = latest.digest if latest else None

        payload = {
            "schema": 1,
            "owner": owner,
            "version_tag": version_tag,
            "staging_tag": staging_tag,
            "run_id": str(run_id),
            "commit": commit,
            "images": sorted(names),
            "latest_baseline": latest_baseline,
        }
        with open(baseline_file, "w", encoding="utf-8", newline="\n") as baseline:
            json.dump(payload, baseline, separators=(",", ":"), sort_keys=True)
            baseline.write("\n")
    else:
        payload = _baseline_payload(
            baseline_file,
            owner=owner,
            version_tag=version_tag,
            staging_tag=staging_tag,
            run_id=run_id,
            commit=commit,
            images=names,
        )
        existing_versions = {
            image: client.get_manifest(f"{owner}/{image}", version_tag)
            for image in names
        }
        if any(manifest is not None for manifest in existing_versions.values()):
            staged = {
                image: client.get_manifest(f"{owner}/{image}", staging_tag)
                for image in names
            }
            missing_stages = [image for image, manifest in staged.items() if manifest is None]
            if missing_stages:
                raise RegistryError(
                    "A version tag exists but this run is missing staging manifests for: "
                    + ", ".join(missing_stages)
                )
            mismatches = [
                image
                for image, manifest in existing_versions.items()
                if manifest is not None and manifest.digest != staged[image].digest
            ]
            if mismatches:
                raise RegistryError(
                    "Existing version tags do not match this run's staged manifests: "
                    + ", ".join(mismatches)
                )

    prepared = {"include": []}
    for image in images:
        enriched = dict(image)
        baseline_digest = payload["latest_baseline"][image["image"]]
        enriched["baseline_latest_digest"] = baseline_digest or ""
        prepared["include"].append(enriched)
    return prepared


def _validate_staging_manifest(manifest: Manifest, repository: str, staging_tag: str) -> None:
    if not isinstance(manifest.body, bytes) or not manifest.body:
        raise RegistryError(f"GHCR returned an invalid staging manifest for {repository}:{staging_tag}.")
    if not isinstance(manifest.digest, str) or not DIGEST_PATTERN.fullmatch(manifest.digest):
        raise RegistryError(f"GHCR returned an invalid staging digest for {repository}:{staging_tag}.")
    computed_digest = f"sha256:{hashlib.sha256(manifest.body).hexdigest()}"
    if manifest.digest != computed_digest:
        raise RegistryError(f"GHCR returned a staging manifest digest mismatch for {repository}:{staging_tag}.")
    if not isinstance(manifest.media_type, str) or manifest.media_type not in SUPPORTED_MEDIA_TYPES:
        raise RegistryError(f"GHCR returned an invalid staging media type for {repository}:{staging_tag}.")
    try:
        payload = json.loads(manifest.body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RegistryError(f"GHCR returned an invalid staging manifest for {repository}:{staging_tag}.") from error
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 2:
        raise RegistryError(f"GHCR returned an invalid staging manifest for {repository}:{staging_tag}.")
    embedded_media_type = payload.get("mediaType")
    if embedded_media_type is not None and embedded_media_type != manifest.media_type:
        raise RegistryError(f"GHCR returned a staging media type mismatch for {repository}:{staging_tag}.")


def _load_staging_record(
    record_file: str,
    *,
    owner: str,
    repository: str,
    image: str,
    staging_tag: str,
    run_id: str,
    commit: str,
) -> dict[str, Any]:
    repair_instruction = "Abandon this version or repair the staging artifact before retrying."
    try:
        with open(record_file, encoding="utf-8") as source:
            record = json.load(source)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise RegistryError(
            f"The GHCR staging digest artifact is missing or invalid. {repair_instruction}"
        ) from error

    if not isinstance(record, dict):
        raise RegistryError(f"The GHCR staging digest artifact has an invalid structure. {repair_instruction}")
    expected = {
        "schema": 1,
        "owner": owner,
        "repository": repository,
        "image": image,
        "staging_tag": staging_tag,
        "run_id": str(run_id),
        "commit": commit,
    }
    for key, value in expected.items():
        if record.get(key) != value:
            raise RegistryError(
                f"The GHCR staging digest artifact does not match this release run ({key}). "
                f"{repair_instruction}"
            )
    if not isinstance(record.get("digest"), str) or not DIGEST_PATTERN.fullmatch(record["digest"]):
        raise RegistryError(f"The GHCR staging digest artifact contains an invalid digest. {repair_instruction}")
    if not isinstance(record.get("media_type"), str) or record["media_type"] not in SUPPORTED_MEDIA_TYPES:
        raise RegistryError(
            f"The GHCR staging digest artifact contains an invalid media type. {repair_instruction}"
        )
    return record


def record_staging_digest(
    client: GHCRClient,
    *,
    owner: str,
    repository: str,
    image: str,
    staging_tag: str,
    run_id: str,
    commit: str,
    expected_digest: str,
    record_file: str,
) -> dict[str, Any]:
    _validate_names(owner, [{"image": image}])
    if not repository or not run_id or not commit:
        raise RegistryError("GHCR staging digest metadata is incomplete.")
    if not isinstance(expected_digest, str) or not DIGEST_PATTERN.fullmatch(expected_digest):
        raise RegistryError("Buildx did not provide a valid image digest; refusing to record the staging manifest.")
    registry_repository = f"{owner}/{image}"
    staged = client.get_manifest(registry_repository, staging_tag)
    if staged is None:
        raise RegistryError(
            f"Staging manifest is missing: {registry_repository}:{staging_tag}. "
            "Abandon this version or repair the staging artifact before retrying."
        )
    _validate_staging_manifest(staged, registry_repository, staging_tag)
    if staged.digest != expected_digest:
        raise RegistryError(
            f"GHCR staging manifest {registry_repository}:{staging_tag} has digest {staged.digest}, "
            f"but Buildx reported {expected_digest}; refusing to record this staging manifest."
        )

    record = {
        "schema": 1,
        "owner": owner,
        "repository": repository,
        "image": image,
        "staging_tag": staging_tag,
        "run_id": str(run_id),
        "commit": commit,
        "digest": staged.digest,
        "media_type": staged.media_type,
    }
    try:
        with open(record_file, "x", encoding="utf-8", newline="\n") as destination:
            json.dump(record, destination, separators=(",", ":"), sort_keys=True)
            destination.write("\n")
    except FileExistsError as error:
        raise RegistryError("Refusing to overwrite an existing GHCR staging digest record.") from error
    return record


def verify_staging_digest(
    client: GHCRClient,
    *,
    owner: str,
    repository: str,
    image: str,
    staging_tag: str,
    run_id: str,
    commit: str,
    record_file: str,
) -> Manifest:
    record = _load_staging_record(
        record_file,
        owner=owner,
        repository=repository,
        image=image,
        staging_tag=staging_tag,
        run_id=run_id,
        commit=commit,
    )
    registry_repository = f"{owner}/{image}"
    staged = client.get_manifest(registry_repository, staging_tag)
    if staged is None:
        raise RegistryError(
            f"Staging manifest is missing: {registry_repository}:{staging_tag}. "
            "Abandon this version or repair the staging artifact before retrying."
        )
    _validate_staging_manifest(staged, registry_repository, staging_tag)
    if staged.digest != record["digest"] or staged.media_type != record["media_type"]:
        raise RegistryError(
            f"The staging manifest for {registry_repository}:{staging_tag} differs from the recorded build digest. "
            "Refusing promotion. Abandon this version or repair the staging artifact before retrying."
        )
    return staged


def _ensure_target(
    client: GHCRClient,
    repository: str,
    target_tag: str,
    staged: Manifest,
    baseline_digest: str | None,
    verify_staging: Callable[[], None] | None = None,
) -> None:
    current = client.get_manifest(repository, target_tag)
    if current is not None and current.digest == staged.digest:
        return
    current_digest = current.digest if current else None
    if current_digest != baseline_digest:
        raise RegistryError(
            f"GHCR target {repository}:{target_tag} has digest {current_digest or '<absent>'}; "
            f"expected staged digest {staged.digest} or preflight baseline {baseline_digest or '<absent>'}."
        )

    try:
        if verify_staging is not None:
            verify_staging()
        response = client.put_manifest(repository, target_tag, staged)
        if response.status != 201:
            raise RegistryError(
                f"GHCR promotion to {repository}:{target_tag} returned HTTP {response.status}."
            )
    except RegistryError as error:
        # A timeout or lost response may happen after GHCR accepted the manifest.
        # Treat it as success only if an authenticated read confirms the exact digest.
        try:
            after_error = client.get_manifest(repository, target_tag)
        except RegistryError as verify_error:
            raise RegistryError(
                f"Promotion outcome for {repository}:{target_tag} is uncertain and verification failed: {verify_error}"
            ) from error
        if after_error is not None and after_error.digest == staged.digest:
            return
        raise RegistryError(
            f"Promotion to {repository}:{target_tag} failed and the staged digest is not present: {error}"
        ) from error

    promoted = client.get_manifest(repository, target_tag)
    if promoted is None or promoted.digest != staged.digest:
        raise RegistryError(
            f"GHCR did not retain the staged digest at {repository}:{target_tag}."
        )


def promote_image(
    client: GHCRClient,
    *,
    owner: str,
    repository: str,
    image: str,
    staging_tag: str,
    version_tag: str,
    run_id: str,
    commit: str,
    record_file: str,
    baseline_latest_digest: str | None,
) -> None:
    _validate_names(owner, [{"image": image}])
    registry_repository = f"{owner}/{image}"
    staged = verify_staging_digest(
        client,
        owner=owner,
        repository=repository,
        image=image,
        staging_tag=staging_tag,
        run_id=run_id,
        commit=commit,
        record_file=record_file,
    )

    def verify_current_staging_digest() -> None:
        current = client.get_manifest(registry_repository, staging_tag)
        if current is None:
            raise RegistryError(
                f"The staging manifest for {registry_repository}:{staging_tag} changed before promotion. "
                "Refusing promotion. Abandon this version or repair the staging artifact before retrying."
            )
        _validate_staging_manifest(current, registry_repository, staging_tag)
        if current.digest != staged.digest or current.media_type != staged.media_type:
            raise RegistryError(
                f"The staging manifest for {registry_repository}:{staging_tag} changed before promotion. "
                "Refusing promotion. Abandon this version or repair the staging artifact before retrying."
            )

    existing_version = client.get_manifest(registry_repository, version_tag)
    if existing_version is not None and existing_version.digest != staged.digest:
        raise RegistryError(
            f"Version tag {registry_repository}:{version_tag} does not match its staged manifest."
        )

    latest = client.get_manifest(registry_repository, "latest")
    if existing_version is not None:
        if latest is None or latest.digest != staged.digest:
            raise RegistryError(
                f"Version tag {registry_repository}:{version_tag} exists but latest does not match the staged digest."
            )
        return

    latest_digest = latest.digest if latest else None
    if latest_digest != staged.digest and latest_digest != baseline_latest_digest:
        raise RegistryError(
            f"GHCR latest for {registry_repository} changed after preflight; refusing to replace unexpected digest."
        )

    # Promote latest first. A matching latest digest is a durable indication that
    # this run already completed the mutable-tag part; version tags are published
    # only after latest has been verified.
    _ensure_target(
        client,
        registry_repository,
        "latest",
        staged,
        baseline_latest_digest,
        verify_current_staging_digest,
    )
    latest = client.get_manifest(registry_repository, "latest")
    if latest is None or latest.digest != staged.digest:
        raise RegistryError(f"GHCR latest verification failed for {registry_repository}.")

    # Version tags are immutable release identifiers. The preflight should find
    # them absent; on a retry, an exact digest match is the only accepted state.
    _ensure_target(
        client,
        registry_repository,
        version_tag,
        staged,
        None,
        verify_current_staging_digest,
    )
    version = client.get_manifest(registry_repository, version_tag)
    if version is None or version.digest != staged.digest:
        raise RegistryError(f"GHCR version-tag verification failed for {registry_repository}:{version_tag}.")


def _client_from_environment() -> GHCRClient:
    username = os.environ.get("GHCR_USERNAME")
    token = os.environ.get("GHCR_TOKEN")
    if not username or not token:
        raise RegistryError("GHCR_USERNAME and GHCR_TOKEN must be set for GHCR access.")
    return GHCRClient(username, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    preflight_parser = commands.add_parser("preflight")
    preflight_parser.add_argument("--owner", required=True)
    preflight_parser.add_argument("--version-tag", required=True)
    preflight_parser.add_argument("--staging-tag", required=True)
    preflight_parser.add_argument("--images-json", required=True)
    preflight_parser.add_argument("--run-id", required=True)
    preflight_parser.add_argument("--attempt", type=int, required=True)
    preflight_parser.add_argument("--commit", required=True)
    preflight_parser.add_argument("--baseline-file", required=True)

    state_parser = commands.add_parser("tag-state")
    state_parser.add_argument("--owner", required=True)
    state_parser.add_argument("--image", required=True)
    state_parser.add_argument("--tag", required=True)

    record_parser = commands.add_parser("record-stage")
    record_parser.add_argument("--owner", required=True)
    record_parser.add_argument("--repository", required=True)
    record_parser.add_argument("--image", required=True)
    record_parser.add_argument("--staging-tag", required=True)
    record_parser.add_argument("--run-id", required=True)
    record_parser.add_argument("--commit", required=True)
    record_parser.add_argument("--expected-digest", required=True)
    record_parser.add_argument("--record-file", required=True)

    verify_parser = commands.add_parser("verify-stage")
    verify_parser.add_argument("--owner", required=True)
    verify_parser.add_argument("--repository", required=True)
    verify_parser.add_argument("--image", required=True)
    verify_parser.add_argument("--staging-tag", required=True)
    verify_parser.add_argument("--run-id", required=True)
    verify_parser.add_argument("--commit", required=True)
    verify_parser.add_argument("--record-file", required=True)

    promote_parser = commands.add_parser("promote")
    promote_parser.add_argument("--owner", required=True)
    promote_parser.add_argument("--repository", required=True)
    promote_parser.add_argument("--image", required=True)
    promote_parser.add_argument("--staging-tag", required=True)
    promote_parser.add_argument("--version-tag", required=True)
    promote_parser.add_argument("--run-id", required=True)
    promote_parser.add_argument("--commit", required=True)
    promote_parser.add_argument("--record-file", required=True)
    promote_parser.add_argument("--baseline-latest-digest", default="")

    args = parser.parse_args(argv)
    try:
        client = _client_from_environment()
        if args.command == "preflight":
            matrix = _read_matrix(args.images_json)
            prepared = preflight(
                client,
                owner=args.owner,
                version_tag=args.version_tag,
                staging_tag=args.staging_tag,
                matrix=matrix,
                run_id=args.run_id,
                attempt=args.attempt,
                commit=args.commit,
                baseline_file=args.baseline_file,
            )
            print(json.dumps(prepared, separators=(",", ":")))
        elif args.command == "tag-state":
            _validate_names(args.owner, [{"image": args.image}])
            state = client.get_manifest(f"{args.owner}/{args.image}", args.tag)
            print("present" if state else "absent")
        elif args.command == "record-stage":
            record = record_staging_digest(
                client,
                owner=args.owner,
                repository=args.repository,
                image=args.image,
                staging_tag=args.staging_tag,
                run_id=args.run_id,
                commit=args.commit,
                expected_digest=args.expected_digest,
                record_file=args.record_file,
            )
            print(json.dumps(record, separators=(",", ":"), sort_keys=True))
        elif args.command == "verify-stage":
            verify_staging_digest(
                client,
                owner=args.owner,
                repository=args.repository,
                image=args.image,
                staging_tag=args.staging_tag,
                run_id=args.run_id,
                commit=args.commit,
                record_file=args.record_file,
            )
        else:
            promote_image(
                client,
                owner=args.owner,
                repository=args.repository,
                image=args.image,
                staging_tag=args.staging_tag,
                version_tag=args.version_tag,
                run_id=args.run_id,
                commit=args.commit,
                record_file=args.record_file,
                baseline_latest_digest=args.baseline_latest_digest or None,
            )
            print(f"Promoted {args.owner}/{args.image}:{args.version_tag} and :latest")
    except (RegistryError, OSError) as error:
        print(f"ghcr_release: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
