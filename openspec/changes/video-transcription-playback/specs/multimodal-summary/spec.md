# Delta for multimodal-summary

## MODIFIED Requirements

### Requirement: Preserve summary contracts and verification coverage

The system MUST preserve existing summary formats, custom-template behavior, APIs, editing/restoration and export flows, transcript segment and word contracts, and clients that do not know visual fields. Viewer timestamp seeking MUST work for both audio and video media through the existing signed `audio_url` compatibility field. Automated tests and documentation MUST cover overlap boundaries, deduplication, budgets, fallback, labels, provenance, timestamp seeking, and compatibility. Intelligence extraction MUST continue to use transcript-only input.

(Previously: Existing consumers and viewer timestamp seeking were preserved without specifying video media.)

#### Scenario: Existing consumer remains compatible

- GIVEN an existing API or UI consumer requests a summary without visual support
- WHEN a multimodal-capable meeting is processed
- THEN the consumer receives the existing contract and can use the existing viewer and timestamp seeking

#### Scenario: Timestamp seeking covers audio and video

- GIVEN a completed audio or video meeting has timestamped transcript words
- WHEN a user activates a transcript timestamp or word
- THEN the existing viewer seeks the corresponding media position and highlights the active word without changing the transcript schema
