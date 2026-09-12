# optional-visual-processing Specification

## Purpose

This capability adds optional, chain-safe video analysis before summary generation. It preserves usable transcript-only processing when video, relevant visual evidence, or a vision provider is unavailable. It MUST NOT require a new media-kind column, fused-context table, new viewer, broad redesign, mandatory external VLM, or automatic provider switching.

## Requirements

### Requirement: Determine media eligibility and retain source media

The system MUST determine video eligibility from the original media MIME/codec or a media probe. Audio-only and inputs without retained video, including YouTube audio inputs, MUST skip visual analysis successfully. Visual analysis MUST process selected candidate frames rather than every frame, and original media MUST remain available until visual processing completes under existing retention and cleanup rules.

#### Scenario: Audio-only or no-video input skips safely

- GIVEN an audio-only, YouTube-derived, or no-video meeting
- WHEN the optional visual stage is reached
- THEN it records a successful visual skip, preserves transcript processing, and does not mark the meeting failed

#### Scenario: Candidate frames use original media

- GIVEN an eligible video with sampled candidate frames
- WHEN visual analysis runs and later retries
- THEN only candidates are sent for analysis and the original media remains available through completion

### Requirement: Make provider and privacy policy explicit

Configuration MUST identify the provider endpoint, model, vision capability, timeout/retry policy, and cloud/egress policy. Local or on-premise endpoints MUST be usable. The system MUST NOT switch providers automatically; an unavailable or disallowed provider MUST take the bounded fallback path.

#### Scenario: Local provider has no implicit cloud switch

- GIVEN a local endpoint is configured and its request fails
- WHEN the visual stage handles the failure
- THEN it records a bounded warning and continues transcript-only without selecting another provider

### Requirement: Preserve chain result and fallback semantics

Every successful, skipped, or fallback visual path MUST return the stable meeting identifier required by downstream processing. Media, provider, malformed-output, and no-relevant-visual conditions MUST continue with transcript-only summarization and bounded telemetry containing statuses, counts, timing, and opaque error identifiers, but no raw media, transcripts, prompts, screenshots, or hidden reasoning.

#### Scenario: Vision worker failure is non-fatal

- GIVEN the vision worker times out after its configured retry bound
- WHEN the chain handles the terminal visual failure
- THEN summary and transcript-only intelligence continue, a bounded warning is recorded, and no sensitive payload is logged

#### Scenario: No relevant visual evidence falls back

- GIVEN eligible video produces only low-relevance or repeated visual candidates
- WHEN context construction completes
- THEN the visual path succeeds with a bounded no-relevant-visual warning and summary uses transcript-only context

### Requirement: Expose lifecycle status through final completion

The processing lifecycle MUST expose `analyzing_video`, `building_context`, and `summarizing` consistently with existing polling and failure behavior. `completed` MUST be set only after summary generation and transcript-only intelligence extraction finish.

#### Scenario: Completion waits for intelligence

- GIVEN visual analysis and summary generation have succeeded or fallen back
- WHEN intelligence extraction is still running
- THEN the meeting remains non-completed until extraction finishes, after which it transitions to `completed`

### Requirement: Make retries and compatibility testable

Transient visual failures MAY retry only within configured bounds. Duplicate delivery or retry MUST converge without duplicate visual results, warnings, or downstream side effects. Existing visual and meeting APIs, web/mobile status mapping, viewer seeking, summary editing, and exports MUST remain backward compatible. Tests and operator documentation MUST cover eligibility, provider/privacy configuration, status transitions, idempotency, retries, fallback, retention, and recovery.

#### Scenario: Duplicate execution converges

- GIVEN the same meeting visual task is delivered more than once
- WHEN idempotent processing completes
- THEN persisted visual results and telemetry converge to one logical run and the chain preserves one stable continuation
