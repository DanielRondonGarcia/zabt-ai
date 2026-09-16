# Video Transcription E2E Specification

## Purpose

Define test-only corrections that make the PR3A upload and PR4 transcript browser suites a trustworthy, repeatable verification signal without changing product behavior, fixtures, or deployment configuration.

## Requirements

### Requirement: PR3A upload tests verify the current upload and MIME contract

The PR3A harness MUST target the exact accessible name `Import a meeting`, inject the missing-MIME case as a browser `File` with `type: ""` through `DataTransfer`, and preserve presign, raw meeting, PUT MIME, storage CORS, and range assertions.

#### Scenario: Upload tests use the current empty-feed action

- GIVEN the local web application is reachable at the explicit E2E base URL
- WHEN the PR3A upload tests open an empty meeting feed
- THEN they locate and activate `Import a meeting` without a fallback locator
- AND the existing upload and storage assertions execute.

#### Scenario: Empty MIME preserves raw metadata and fallback transport types

- GIVEN an upload fixture has an actually empty browser `File.type`
- WHEN PR3A uploads the file and captures the requests
- THEN the meeting payload retains an empty `content_type`
- AND presign and PUT use the existing `audio/mpeg` fallback while CORS and range contracts remain asserted.

### Requirement: PR4 tests synchronize transcript controls and preserve media coverage

The PR4 harness MUST await the semantic `role="tab"` named `Transcript` before clicking, use exact accessible names for `Play` and `Pause`, and verify video/audio playback, seeking and highlighting, lifecycle cleanup, layout reservation, media errors, and accessibility.

#### Scenario: Supported video transcript interaction is deterministic

- GIVEN the transcript page renders its tabs asynchronously
- WHEN PR4 opens the transcript and exercises supported video playback
- THEN the exact Transcript tab and Play/Pause controls are used after they are available
- AND duration, playback rate, seeking, word highlighting, and the 375px layout reservation are verified.

#### Scenario: Audio and media-error states remain usable

- GIVEN audio regression, expired-media, or unsupported-media conditions
- WHEN PR4 exercises playback and cleanup paths
- THEN audio identity/lifecycle behavior and media errors are verified without a harness timeout
- AND the existing status message, transcript words, and required accessibility checks remain usable.

### Requirement: Focused runs are explicit and deterministically torn down

Every PR3A, PR4, and combined verification run MUST set `E2E_BASE_URL=http://localhost:3001`, execute the prescribed slices independently and together, and deterministically close browser/page resources and stop and join module-owned media-server processes on success or failure.

#### Scenario: Prescribed focused commands use the active local stack

- GIVEN the local Docker stack and Chromium prerequisites are available
- WHEN PR3A, PR4, and the combined focused commands are run
- THEN each command explicitly supplies `E2E_BASE_URL=http://localhost:3001`
- AND the results cover 10 focused tests without relying on implicit URL defaults.

#### Scenario: Failure teardown leaves no owned resources

- GIVEN a focused test or assertion fails
- WHEN module teardown runs
- THEN browser/page resources are closed and the media-server child is stopped and joined
- AND no orphaned module-owned process remains.

### Requirement: Acceptance remains test-only and split into bounded stacked slices

The combined change MUST stay within the approved 600 changed-line `exception-ok` limit, deliver PR3A first and PR4 stacked to main, and leave product behavior unchanged. It MUST NOT modify the fixture, packages, cloud or mobile configuration, or transcript schema.

#### Scenario: PR boundaries and size are reviewable

- GIVEN the proposed PR3A and PR4 diffs
- WHEN acceptance boundaries are evaluated
- THEN PR3A contains only its upload-harness correction and support, PR4 contains only its transcript-harness correction and support, and the combined authored diff is at most 600 changed lines
- AND PR4 is stacked after PR3A toward main.

#### Scenario: Protected artifacts remain unchanged

- GIVEN the E2E corrections are applied
- WHEN the change is compared with its base
- THEN product code, fixture content, package dependencies, cloud/mobile configuration, and transcript schema are unchanged.
