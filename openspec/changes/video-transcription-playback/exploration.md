---
status: explored
executive_summary: >-
  Video uploads are already accepted and transcribed through the existing media
  pipeline, but the web transcription player always mounts a hidden audio
  element and the meeting contract does not expose a reliable media kind. The
  lowest-risk implementation is to extend the existing StickyMediaPlayer to
  support a visible native video element while preserving its shared timeline,
  seeking, playback-rate, and transcript-highlighting behavior, after adding an
  additive normalized media-type field to the meeting response.
artifacts:
  - path: openspec/changes/video-transcription-playback/exploration.md
    store: openspec
    type: exploration
next_recommended: sdd-research
risks:
  - The primary UploadModal sends the MIME type to presigned upload but omits it from meeting creation.
  - MeetingRead exposes a signed field named audio_url and no media-kind field.
  - Browser playback depends on codec support, object Content-Type, CORS, and byte-range behavior.
  - The current frontend has no unit-test runner or media-player test coverage.
  - openspec/config.yaml is absent even though the native dispatcher selected OpenSpec.
skill_resolution:
  mode: paths-injected
  injected_skill_paths:
    - C:\Users\daniel.rondon\.config\opencode\skills\sdd-explore\SKILL.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\sdd-phase-common.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\openspec-convention.md
---

## Exploration: Video Transcription Playback

### Current State

The requested change is a web meeting-detail presentation enhancement. The existing upload and transcription pipeline already accepts video files, so the main gap is media delivery and playback presentation rather than speech recognition.

The verified end-to-end path is:

`UploadModal.startUpload` → `POST /meetings/presigned-upload` → `POST /meetings/` → direct S3/MinIO `PUT` → webhook or `confirm-upload` → `dispatch_pipeline` → `stage_download` → `stage_transcribe` → `GET /meetings/{id}` → `MeetingDetailPage` → `StickyMediaPlayer` + `TranscriptViewer`.

#### Upload and media metadata

- `frontend-2/app/components/upload-modal.tsx:139-177` accepts `audio/*` and `video/*`, sends `item.file.type` to the presigned-upload endpoint, and uploads the file with the same `Content-Type`. Its meeting-creation payload currently omits `content_type`.
- `frontend-2/app/lib/api.ts:147-179` contains a separate `uploadMeeting` helper that does include `content_type` when creating the meeting, but the primary dashboard flow is wired through `UploadModal` (`HomePage` → `UploadModal`).
- `backend/app/api/v1/endpoints/meetings.py:147-220` accepts `content_type` on `MeetingCreateWithKey` and stores it in `Meeting.visual_breakdown_params` under `content_type` when provided. `backend/app/services/storage.py:111-120` and `:267-276` bind the MIME type into the presigned PUT request; the object key retains the original filename suffix.
- `backend/app/models/base.py:138-188` has no first-class media-kind or MIME column on `Meeting`. `visual_breakdown_params` is a JSONB field introduced for visual processing, so it is not a stable public viewer contract and is absent for legacy or incomplete upload records.

#### Transcription, duration, and transcript delivery

- `backend/app/worker.py:214-371` runs the provider, writes each `ResultSegment` to `TranscriptSegment`, serializes word timestamps into the `words` JSONB field, stores `transcript_text`, and sets `Meeting.duration_seconds` from `TranscriptionResult.audio_duration_seconds`.
- `backend/app/services/transcription/types.py:17-43` defines `WordTimestamp`, `ResultSegment`, and `TranscriptionResult`; segment and word timings are floating-point seconds and are available for audio and video inputs because the transcription provider receives the media's audio track.
- `backend/app/api/v1/endpoints/meetings.py:60-145` builds the detail response. It maps `TranscriptSegment.start_time/end_time/text/speaker/words` to `TranscriptSegmentRead`, generates a signed public download URL, and returns it in the compatibility field `audio_url`.
- `backend/app/models/base.py:190-198` stores `TranscriptSegment` timings and word dictionaries; `:263-298` defines `MeetingRead`, which currently exposes `file_path`, `duration_seconds`, `segments`, and `audio_url`, but no media type.
- `packages/shared/src/types.ts:5-17` and `:26-64` mirror the API with `TranscriptWord`, `TranscriptSegment`, and `Meeting`. The shared contract also calls the generic signed media URL `audio_url` and has no `media_type`/`content_type` field.

#### Existing web player and transcript behavior

- `frontend-2/app/(dashboard)/meetings/[id]/page.tsx:576-709` renders the transcript tab. It uses `TranscriptViewer` for the original timestamped transcript, keeps the transliteration view separate, and mounts `StickyMediaPlayer` only when the meeting is completed, has a file path, and the transcript tab is active.
- `frontend-2/app/components/sticky-media-player.tsx:10-177` owns the current timeline and controls. It maps speaker segments onto a progress bar, sends timeline clicks to `seekRequest`, handles rewind and playback speed, tracks native media time with `requestAnimationFrame`, and currently mounts only a hidden `<audio>` element using `meeting.audio_url || meeting.file_path`.
- `frontend-2/app/components/transcript-viewer.tsx:32-106` highlights words from `useTranscriptStore.currentTime` and turns word/timestamp clicks into seek requests. The segment timeline uses the same `start`/`end` values as the transcript rows.
- `frontend-2/app/lib/use-transcript-store.ts:5-25` is already media-agnostic state: current time, duration, playing state, and seek requests. No existing `<video>` player or reusable video playback component was found elsewhere in the repository.
- `zabt-mobile/components/meeting/transcript-tab.tsx:16-48` only renders mobile transcript text and timestamps; the mobile meeting detail has no equivalent audio timeline/player. The request therefore most directly matches the web transcription area and should not silently expand to mobile parity.

#### Processing and visual-analysis constraints

- `backend/app/worker.py:953-1116` passes persisted `content_type`/media hints to the optional visual worker, but this is for visual analysis, not viewer rendering.
- `zabt-vision-worker/zabt_vision/pipeline/run.py:143-164` classifies audio/video/youtube hints, and `:268-300` probes the downloaded object with `ffprobe` to skip audio-only inputs. This confirms that media-kind detection exists in the vision pipeline but is not exposed through the meeting API for the frontend.
- The existing OpenSpec requirements in `openspec/specs/optional-visual-processing/spec.md` and `openspec/specs/multimodal-summary/spec.md` explicitly preserve existing viewer seeking and APIs, and prohibit introducing a new viewer or broad redesign. The requested work should stay within that compatibility boundary.

### Affected Areas

- `frontend-2/app/components/sticky-media-player.tsx` — primary implementation point; generalize the native media ref/events and add a visible video presentation without duplicating timeline, seek, speed, and transcript synchronization logic.
- `frontend-2/app/(dashboard)/meetings/[id]/page.tsx` — keep the transcript-tab mounting rules and reserve enough layout space for a visible video surface so it does not obscure transcript rows behind the fixed player.
- `frontend-2/app/components/transcript-viewer.tsx` — likely unchanged behavior, but its word/timestamp seek contract must remain covered when the underlying element changes from audio to video.
- `frontend-2/app/lib/use-transcript-store.ts` — likely unchanged; verify the shared time/seek state works with `HTMLMediaElement` events and does not leak state between meetings.
- `frontend-2/app/lib/api.ts` and `packages/shared/src/types.ts` — add an additive normalized media-kind field while retaining `audio_url` for older consumers; keep transcript segment and word contracts unchanged.
- `frontend-2/app/components/upload-modal.tsx` — forward the selected file MIME type in the meeting-creation request, or otherwise ensure the backend can derive a reliable media kind for the primary upload path.
- `backend/app/api/v1/endpoints/meetings.py` — expose the normalized media kind from the persisted upload metadata and continue returning the existing signed URL and transcript fields.
- `backend/app/models/base.py` and `backend/alembic/versions/l0m1n2o3p4q5_add_visual_breakdown.py` — only involved if the design chooses a new durable `Meeting` column; first-slice reuse of the existing JSONB metadata avoids a migration but needs legacy fallback rules.
- `backend/app/services/storage.py` — verify stored object MIME metadata, browser CORS, signed-URL expiry, and range requests for video playback; no new storage provider abstraction is indicated.
- `backend/tests/integration/test_uploads.py`, `backend/tests/contract/test_meetings.py`, and `tests/e2e/test_meeting_upload.py` — existing upload/API/UI coverage to extend with video MIME propagation and response compatibility. There is no frontend component-test suite and no existing `StickyMediaPlayer` test.

### Approaches

1. **Extend the existing media player with an explicit media kind** — Preserve `StickyMediaPlayer` as the single timeline/control owner, use an `HTMLMediaElement` ref, render a native `<video>` surface for `media_type === "video"`, and keep the current hidden `<audio>` path for audio. Add an additive API/shared field derived from upload metadata, with a safe fallback for legacy meetings.
   - Pros: reuses the proven timeline, speaker-segment visualization, seek store, word highlighting, playback speed, and signed media URL; keeps the change aligned with the existing viewer and OpenSpec compatibility requirements.
   - Cons: requires fixing MIME propagation in the primary upload path, defining a legacy classification fallback, and handling the larger fixed-player layout and browser media errors.
   - Effort: Medium

2. **Create a separate video-transcript player and share only low-level helpers** — Leave the audio player untouched and build a video-specific component, extracting common time/seek logic only where duplication becomes obvious.
   - Pros: isolates audio regressions and permits video-specific responsive layout and fallback messaging.
   - Cons: duplicates or splits the existing control/timeline behavior, increases the chance that audio and video seeking/highlighting diverge, and adds more UI surface than the request requires.
   - Effort: Medium-High

### Recommendation

Choose the unified-player approach. First make media kind an explicit, additive meeting-response field (`audio`/`video`/nullable) backed by the upload MIME value already accepted by the backend; retain `audio_url` as the signed generic media URL so existing clients remain compatible. Because the dashboard's `UploadModal.startUpload` currently omits `content_type` from `POST /meetings/`, fix that contract or define a backend fallback before the player relies on the field. Legacy records without stored MIME should fall back conservatively to the current audio presentation, with extension/probe-based detection considered only as a bounded compatibility fallback.

Within `StickyMediaPlayer`, use one `HTMLMediaElement` event/seek loop and conditionally render a visible native `<video>` for video meetings. Keep the current custom timeline and transcript seek behavior, use native playback rather than a new media library, and preserve the audio DOM path and `audio_url` field for existing clients. The implementation should remain web-only unless a later requirement explicitly asks for mobile playback, because mobile currently has no comparable audio player to extend.

### Risks

- **MIME propagation gap:** The primary `UploadModal` sends `item.file.type` to presigned storage and the PUT request but not to meeting creation, so `visual_breakdown_params.content_type` may be missing for exactly the videos users upload through the main UI.
- **Contract naming:** `audio_url` is the existing public field for the signed object URL. Renaming it to `video_url` or `media_url` without preserving the old field would break older web/mobile consumers.
- **Legacy records:** Older meetings, direct API-created meetings, and YouTube-derived audio have no reliable frontend media kind. The fallback must not incorrectly render a video element for audio-only data.
- **Browser compatibility:** A video that ffmpeg/ffprobe can process may still use a browser-unsupported codec or container. MP4/H.264/AAC should be tested explicitly; unsupported media needs an accessible error or audio-only fallback.
- **Storage delivery:** HTML video playback requires the signed URL to remain valid, correct object `Content-Type`, CORS permission, and HTTP range requests. The existing audio path exercises some of this but does not prove video behavior.
- **Fixed-player layout:** The current player is fixed to the viewport bottom and the transcript card only reserves `pb-20`. A visible video surface may cover transcript content unless its height, responsive behavior, and bottom spacing are designed together.
- **State lifecycle:** `useTranscriptStore` is global and does not reset automatically on route/meeting changes. A video implementation must avoid stale time/seek state when opening a different meeting.
- **Test coverage:** The frontend package exposes build/lint scripts but no unit-test script or existing media-player tests. Browser-level coverage and TypeScript/build validation will be important.
- **Planning context:** `openspec/config.yaml` was not present, so project-specific OpenSpec rules could not be read; the native dispatcher-selected OpenSpec store and existing specs were used without creating configuration files.

### Ready for Proposal

Yes. The repository evidence is sufficient for a proposal focused on the web transcription view. The proposal should explicitly decide the normalized API field and legacy fallback, correct the primary upload MIME propagation gap, define the visible video placement within the sticky player, state browser codec/range/CORS behavior, and specify audio-regression plus video-playback tests. It should not expand scope to visual analysis, transcript schema changes, or mobile playback unless separately approved.
