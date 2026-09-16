# video-transcription-playback Specification

## Purpose

The web transcription view MUST show uploaded video with a visible native video surface while preserving the audio experience, transcript contract, and signed URL compatibility. This is web-only and requires no database migration.

## Requirements

### Requirement: Expose normalized media metadata compatibly

The meeting read API and shared types MUST add nullable `media_type`: `audio`, `video`, or `null`, derived from stored MIME (`audio/*` or `video/*`). Missing or unknown MIME MUST return `null`. `audio_url` MUST remain the signed generic URL; fields remain compatible. No migration is permitted.

#### Scenario: Known video and legacy records

- GIVEN a `video/mp4` meeting and a legacy meeting with missing MIME
- WHEN meeting details are requested
- THEN the first returns `media_type: "video"`, the second `null`, and both retain `audio_url`

### Requirement: Propagate primary upload MIME safely

The primary web upload MUST pass the selected file MIME to meeting creation as it does to presigned upload. Empty, unknown, or omitted MIME MUST remain valid, use conservative legacy behavior, and never infer kind from extension.

#### Scenario: Primary video upload

- GIVEN the primary upload modal selects a video file
- WHEN upload and meeting creation complete
- THEN metadata enables `media_type: "video"` and the presigned PUT contract is unchanged

#### Scenario: Missing browser MIME

- GIVEN an older client or browser provides no reliable MIME
- WHEN the meeting is created and opened
- THEN creation succeeds, `media_type` is `null`, and the web view uses the audio presentation

### Requirement: Render the correct web media surface

For `media_type: "video"`, the web transcription view MUST render visible native video from `audio_url`; `audio` and `null` MUST retain the current audio path and controls. Transcript segments, words, speakers, and timestamps MUST remain unchanged.

#### Scenario: Video transcription tab

- GIVEN a completed video meeting with a valid signed URL
- WHEN the user opens the transcription tab
- THEN video is visible with usable duration and the transcript remains available

### Requirement: Synchronize one native media control path

One native media control path MUST own timeline progress, seeking, playback rate, current-time state, word highlighting, and transcript click-to-seek for audio and video.

#### Scenario: Playback and transcript synchronization

- GIVEN an audio or video meeting with word timestamps
- WHEN playback, speed, timeline, or transcript-word interaction occurs
- THEN media position, timeline, current-time state, and highlighted word stay synchronized

### Requirement: Preserve layout and lifecycle state

The fixed player MUST reserve space and MUST NOT obscure transcript rows. On meeting, URL, route, or lifecycle changes, listeners, playback state, and pending seeks MUST be cleaned so state cannot leak.

#### Scenario: Navigate between meetings

- GIVEN the user played one meeting and opens another
- WHEN the second view mounts or media changes
- THEN the first media stops, stale time/seeks clear, and transcript content remains unobscured

### Requirement: Handle playback failures accessibly and non-blockingly

Unsupported codecs, signed URL failure/expiry, wrong `Content-Type`, unavailable ranges, CORS, or load errors MUST show an accessible, non-blocking status. The transcript MUST remain usable. Tests MUST verify a supported fixture, signed GET, CORS, and range/`206` behavior.

#### Scenario: Media cannot load

- GIVEN a video URL expires or the browser rejects its codec/storage response
- WHEN media loading fails
- THEN an accessible error is announced, transcript use is unblocked, and timestamp interactions remain available

### Requirement: Verify compatibility and bounded scope

Focused backend/API, browser, and E2E tests MUST cover MIME normalization/propagation, nullable legacy behavior, URL compatibility, audio regression, visible video, synchronized controls, transcript seeking/highlighting, layout, cleanup, and errors. The change MUST NOT add mobile parity, visual analysis, transcript schema changes, re-encoding, broad redesign, or a new media library.

#### Scenario: Regression and scope acceptance

- GIVEN focused API, browser, and E2E suites run with audio and browser-supported video fixtures
- WHEN the suites complete
- THEN compatibility and video behaviors pass without migration or out-of-scope changes
