# Proposal: Video Transcription Playback

## Intent

Video uploads are transcribed, but the web transcription tab mounts hidden audio. Display video while retaining audio behavior, timeline, seeking, playback-rate, and transcript highlighting.

## Scope

### In Scope
- Add nullable `media_type: audio | video | null`; retain `audio_url` as the signed generic media URL.
- Forward MIME from the primary `UploadModal`; define safe legacy fallback.
- Unify native media playback, fixed layout, errors, storage checks, and focused tests.

### Out of Scope
- Mobile; visual-analysis/transcription-pipeline changes; transcript schema; re-encoding; broad redesign; or new media library.

## Capabilities

### New Capabilities
- `video-transcription-playback`: Web video playback synchronized with transcript.

### Modified Capabilities
- `multimodal-summary`: Timestamp seeking covers audio and video; transcript contract remains unchanged.

## Approach

- Backend keeps `content_type` in `visual_breakdown_params` JSONB and derives `media_type` from `audio/*` or `video/*`; missing/unknown values return `null`. No migration/new column. `MeetingRead` adds it; `audio_url` stays. Legacy `null` uses audio; no extension guessing.
- `UploadModal` includes `content_type: item.file.type` in meeting creation; presigned PUT stays unchanged. Shared types are additive.
- `StickyMediaPlayer` owns one `HTMLMediaElement`: hidden `<audio>` or visible native `<video>` uses the existing timeline, seek, rate, and transcript sync. Reserve fixed-player space; reset on meeting/media changes.
- Show accessible, non-blocking network/signed-URL/codec errors; keep the transcript usable. Verify object `Content-Type`, signed expiry, CORS, and range/`206` behavior.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/api/v1/endpoints/meetings.py`, `packages/shared/src/types.ts` | Modified | Normalize/expose the additive field. |
| `frontend-2/app/components/upload-modal.tsx`, `sticky-media-player.tsx`, meeting page | Modified | MIME, video sync, and fixed layout. |
| `backend/tests/**`, `tests/e2e/test_meeting_upload.py` | Modified | API, audio/video, error, MIME, and lifecycle coverage. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Codec or storage headers prevent playback | High | MP4 fixture, CORS/range checks, accessible error state. |
| Legacy records lack MIME | Med | `null` and the proven audio fallback; never guess. |
| Video covers transcript or leaks state | Med | Overlap, route-change, cleanup tests. |

## Rollback Plan

Revert the additive API/type, upload, and player changes. No migration or data rewrite means `audio_url` and legacy audio playback remain available.

## Dependencies

- Storage must provide correct video `Content-Type`, signed GETs, CORS, and byte ranges; tests need a browser-supported video fixture.

## Success Criteria

- [ ] Audio regression confirms unchanged controls, seeking, and highlighting.
- [ ] Browser tests show visible video, synchronized controls/transcript clicks, usable errors, and no overlap.
- [ ] API tests confirm MIME propagation, nullable field, legacy fallback, and `audio_url` compatibility.
