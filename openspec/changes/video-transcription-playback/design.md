# Design: Video Transcription Playback

## Technical Approach

Additive MIME metadata will travel from the primary web upload into the existing `Meeting.visual_breakdown_params` JSONB, then be normalized by the meeting-detail mapper as `media_type`. The existing signed `audio_url` remains the generic media URL. The transcription page will render one shared custom control path backed by one keyed `HTMLMediaElement`: hidden `<audio>` for `audio`/`null`, visible `<video>` for `video`.

## Architecture Decisions

| Decision | Alternatives considered | Rationale |
|---|---|---|
| Derive `media_type` from JSONB at response time | New database column; filename/extension detection | No migration or rewrite is required, unknown MIME stays safely nullable, and extensions are unreliable. |
| One `HTMLMediaElement` ref and shared controls | Separate audio/video controllers; native controls plus custom controls | Keeps seeking, rate, duration, playback, and transcript synchronization identical and prevents duplicated or divergent state. |
| Page-owned space reservation with a measured fixed player | Fixed player without a spacer; ordinary in-flow player | Preserves the existing sticky behavior while guaranteeing the last transcript rows remain visible at narrow web widths. |

## Data Flow

```text
File.type ──→ UploadModal ──→ POST /meetings/ content_type
    │              └────────── existing presigned PUT unchanged
    └────────────────────────── Meeting.visual_breakdown_params JSONB
                                      │
                       normalize audio/* / video/* / unknown
                                      │
          MeetingRead.media_type + signed audio_url ──→ shared Meeting type
                                      │
              MeetingDetailPage ──→ StickyMediaPlayer
                                      │
              HTMLMediaElement ↔ transcript store ↔ TranscriptViewer
```

## File Changes

| File | Action | Description |
|---|---|---|
| `backend/app/models/base.py` | Modify | Add nullable `media_type` to `MeetingRead`; do not alter the table model. |
| `backend/app/api/v1/endpoints/meetings.py` | Modify | Normalize stored MIME, persist the raw create payload in existing JSONB, and expose the field from create/detail mappings while preserving `audio_url`. |
| `packages/shared/src/types.ts` | Modify | Add `MediaType` and `media_type: MediaType \| null`; `frontend-2/app/lib/api.ts` continues to re-export it. |
| `frontend-2/app/components/upload-modal.tsx` | Modify | Send raw `item.file.type` in meeting creation; retain current presigned-request/PUT fallback behavior for empty browser MIME. |
| `frontend-2/app/lib/use-transcript-store.ts` | Modify | Add reset/media-identity handling so current time, duration, playing state, and pending seeks cannot cross meetings. |
| `frontend-2/app/components/sticky-media-player.tsx` | Modify | Own the keyed `HTMLMediaElement`, shared controls, lifecycle cleanup, visible video surface, and accessible non-blocking errors. |
| `frontend-2/app/(dashboard)/meetings/[id]/page.tsx` | Modify | Mount the player only for the transcription tab, reserve measured bottom space, and reset it on meeting/media changes. |
| `backend/tests/contract/test_meetings.py`, `tests/e2e/test_meeting_upload.py`, `tests/e2e/test_transcript_viewer.py` | Modify | Add API, MIME propagation, audio regression, video, seeking/highlighting, layout, cleanup, and error coverage. |
| `tests/e2e/fixtures/short-video.mp4` | Create | Browser-supported fixture, served with range-capable test headers. |

## Interfaces / Contracts

The backend helper accepts only a dict-like JSONB value, trims/lowercases `content_type`, removes optional parameters, and returns `"audio"`, `"video"`, or `None` only for `audio/*` or `video/*`; it never examines filenames. The create payload keeps `content_type` optional. The TypeScript contract is:

```ts
export type MediaType = "audio" | "video";
media_type: MediaType | null;
```

`StickyMediaPlayer` uses `useRef<HTMLMediaElement>`, a `mediaKey` based on meeting ID, URL, and media type, and `onLoadedMetadata`, `onPlay`, `onPause`, `onEnded`, `onTimeUpdate`, and `onError`. A media-key effect pauses the old element, cancels RAF/listeners, clears pending seeks and store state, resets rate/error, and keys the element so audio/video DOM types cannot be reused incorrectly. `play()` failures are caught. TranscriptViewer remains the timestamp/word seek client; transcript segments and words are unchanged.

Storage acceptance requires a valid signed GET, correct object `Content-Type` (for example `video/mp4`), browser CORS, `Accept-Ranges: bytes`, and working `206`/`Content-Range`. Errors announce a generic `role="status"`/`aria-live` message without disabling transcript rows or timestamp buttons; no signed URL is logged. Playback errors may emit a PostHog event containing only media kind and browser error code.

## Testing Strategy

| Layer | What to test | Approach |
|---|---|---|
| Unit/API | Known, missing, empty, parameterized, and unknown MIME; JSONB persistence; nullable legacy response; signed `audio_url` compatibility | Pytest contract tests with storage URL stubs. |
| Upload/integration | Video MIME reaches meeting creation while presigned PUT remains unchanged | Playwright request interception plus API assertions. |
| Browser/E2E | Visible video, audio regression, play/pause/rate/seek, word highlighting, transcript visibility, navigation cleanup, expired/unsupported media | Playwright with audio and `short-video.mp4`; assert element type, `currentTime`, classes, bounds, pause/reset, and accessible status. |

## Sequencing

1. Add MIME normalization, API mapping, and contract tests.
2. Add the shared type and upload creation payload, then verify raw MIME propagation.
3. Refactor store/player/page lifecycle and layout; run audio regression before video scenarios.
4. Run browser playback plus storage header/range checks and release as one compatible web change.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary.

## Migration / Rollout

No migration required. Deploy backend normalization, shared types, and web changes together; old records return `null` and use audio presentation. Rollback is a code revert with no data rewrite. Keep visual analysis, mobile parity, re-encoding, transcript-schema changes, extension guessing, and new media libraries out of scope.

## Open Questions

None; storage header/range compliance is a deployment and test prerequisite, not a product decision.
