# Tasks: Multimodal Meeting Summary

## Review Workload Forecast

Estimated changed lines: 850–1,100 authored lines
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: High
Delivery strategy: ask-on-risk
Suggested split: PR1 → PR2 → PR3

> Path warning: proposal path `frontend-2/app/components/progress-steps.tsx` (read-only) is stale; use `frontend-2/app/components/ui/progress-steps.tsx`; proposal unchanged.

### Suggested Work Units

| Unit | PR | Goal | Focused test | Runtime harness | Rollback |
|---|---|---|---|---|---|
| 1 | PR 1 | Context/privacy contracts; code → typed unit boundary | `cd backend && uv run pytest tests/unit/test_multimodal_context.py tests/unit/test_vision_client.py -q` | N/A: pure unit boundary | Context/config/client files |
| 2 | PR 2 | Chain-safe visual processing after Unit 1 | `cd backend && uv run pytest tests/unit/test_visual_breakdown_task.py tests/integration/test_visual_breakdown_endpoints.py -q`; `cd zabt-vision-worker && uv run pytest tests/test_pipeline_run.py tests/test_server.py -q` | `docker compose --profile local --profile vision up -d`; audio/video/timeout | Worker/vision/convergence files |
| 3 | PR 3 | Summary/compatibility/docs after Unit 2 | `npm run build:shared && npm run build:web`; `cd zabt-mobile && npm test -- --runInBand` | Upload, poll, seek, edit, export | Summary/UI/docs wiring |

## Phase 1: Contracts and RED Tests

- [x] 1.1 RED: Add `backend/tests/unit/test_multimodal_context.py` for interval boundaries, IDs/timestamps, provenance, dedupe, relevance, budgets, fragments, uncertainty, and overflow.
- [x] 1.2 RED: Extend `backend/tests/unit/test_vision_client.py` for `POST /run` timeout, non-2xx, malformed/no-secret, bounded retry, and no switching.
- [x] 1.3 RED: Extend `backend/tests/unit/test_visual_breakdown_task.py` for duplicate/retry convergence, stable ID, deduped warning/telemetry/notification, and missing-meeting fatality.
- [x] 1.4 RED: Extend `zabt-vision-worker/tests/test_pipeline_run.py` and `zabt-vision-worker/tests/test_server.py` for audio/no-video, invalid input, nonzero `ffprobe`, and sanitized fallback.
- [x] 1.5 Create `backend/app/services/multimodal_context.py` with frozen chunks, true half-open overlap, dedupe, provenance, hierarchical budgets, completeness, and no silent loss.
- [x] 1.6 Update `backend/app/core/config.py`, `backend/app/services/visual_breakdown/types.py`, `backend/app/services/visual_breakdown/vision_client.py`, `backend/app/services/storage.py`, `zabt-vision-worker/zabt_vision/settings.py`, and `zabt-vision-worker/zabt_vision/inference/factory.py` for private capability/egress, retries, and fresh URLs.

## Phase 2: Chain and Core Processing

- [x] 2.1 Update `backend/app/worker.py`, `backend/app/services/meeting.py`, `backend/app/services/visual_segments.py`, and `backend/app/models/base.py` for `stage_optional_visual_breakdown`, ID-only results, epoch/Redis leases, atomic convergence, media retention, `analyzing_video`/`building_context`/`summarizing`, and fallback; no migration/fused table/media kind.
- [x] 2.2 Update `zabt-vision-worker/zabt_vision/pipeline/run.py`, `zabt-vision-worker/zabt_vision/pipeline/video_native.py`, and `zabt-vision-worker/zabt_vision/types.py` for MIME/argv-only `ffprobe`, sampled candidates, YouTube/audio skips, and sanitized outcomes.
- [x] 2.3 Update `backend/app/services/ai_agent.py` and `backend/app/services/template_seed.py` for ephemeral hierarchical context, labeled spoken/visual/uncertainty evidence, source refs, complete budgets, custom templates, and no chain-of-thought.
- [x] 2.4 Keep `backend/app/services/meeting_intelligence.py` transcript-only; update `backend/app/api/v1/endpoints/meetings.py` for completion-after-intelligence and existing API/signed-URL fallback contracts.

## Phase 3: Compatibility and Documentation

- [x] 3.1 Update `packages/shared/src/types.ts`, `packages/shared/src/enums.ts`, `frontend-2/app/lib/api.ts`, `frontend-2/app/lib/stage-utils.ts`, `frontend-2/app/components/ui/progress-steps.tsx`, `frontend-2/app/components/status-badge.tsx`, `frontend-2/app/components/transcript-viewer.tsx`, `frontend-2/app/components/sticky-media-player.tsx`, `frontend-2/app/(dashboard)/meetings/[id]/page.tsx`, and `zabt-mobile/lib/stage-utils.ts`; map statuses, preserve seeking/edit/restore/export, without a new viewer.
- [x] 3.2 Extend `backend/tests/contract/test_meetings.py`, `backend/tests/integration/test_visual_breakdown_endpoints.py`, `zabt-vision-worker/tests/test_settings.py`, and `zabt-vision-worker/tests/test_types.py` for fallback, completion ordering, provenance, and compatibility.
- [x] 3.3 Update `.env.example`, `docs/configuration.md`, `docs/self-hosting.md`, and `docker-compose.yml` with disabled-by-default local/private deployment, explicit cloud/egress controls, and recovery guidance.

## Phase 4: Verification

- [x] 4.1 Attempted `cd backend && uv run pytest tests/unit tests/integration tests/contract -q` and `cd zabt-vision-worker && uv run pytest -q`; the exact host paths remain limited by the malformed inherited `DATABASE_URL` and host ffmpeg/dependency setup. `npm run build:shared && npm run build:web` exited 0 with 10/10 static pages. Under the explicit maintainer decision, the supported Python 3.11 container equivalents are accepted evidence: backend **120 passed** and vision-worker **57 passed, 1 skipped, 2 warnings**.
- [x] 4.2 Verified audio-only/YouTube/video, timeout/malformed/retry, and long-meeting scenarios with bounded warnings, complete source assignment, transcript-only intelligence, no sensitive telemetry, and `completed` only after extraction. The deterministic local-video scenario using existing `sample.mp4` is accepted under the explicit maintainer decision; provider-backed real-video coverage remains an explicitly accepted unavailable warning because `demo_short.mp4` and an opted-in Ollama model are unavailable.
