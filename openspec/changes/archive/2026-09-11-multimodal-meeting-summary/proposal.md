# Proposal: Feature: Multimodal Meeting Summary

## Intent

Make meeting summaries use relevant visual evidence from video while preserving transcript-only reliability. Zabt already stores timestamped `TranscriptSegment` and `VisualSegment` data, but the visual stage is disconnected from the processing chain and summaries currently receive transcript text only.

## Scope

### In Scope
- Add an optional, chain-safe visual stage before summary generation; detect video conservatively from original media MIME/codec or an `ffprobe`-style probe, skip audio-only/YouTube inputs, expose `analyzing_video`/`building_context`/`summarizing`, and complete after intelligence extraction.
- Add a pure dynamic temporal-fusion/context-builder layer with true bidirectional half-open overlap, deduplication, bounded hierarchical chunks, source timestamps/IDs, relevance, provenance, and uncertainty.
- Adapt summary prompts/templates and configuration for `SPOKEN CONTENT`, `VISUAL CONTEXT`, and uncertainty; preserve transcript-only fallback, storage/privacy boundaries, APIs, current viewer seeking, and add focused tests/docs.

### Out of Scope
- A fused-context table, new media-kind column, or other migration.
- Multimodal intelligence extraction, broad UI redesign, a new viewer, or mandatory external VLM/provider implementation.
- Automatic provider switching or all-frame VLM analysis.

## Capabilities

### New Capabilities
- `multimodal-summary`: Generate summaries from bounded, timestamped spoken and visual context with conservative evidence handling and hierarchical synthesis.
- `optional-visual-processing`: Integrate video analysis, status/lifecycle behavior, provider/privacy configuration, idempotent retries, and transcript-only fallback.

### Modified Capabilities
- None; no existing `openspec/specs/` capabilities were found.

## Approach

Wrap or revise `stage_visual_breakdown` so every successful path returns the chain’s `meeting_id`; convert media/provider/no-video failures into bounded warnings, then build context dynamically from persisted segments. Keep visual relevance conservative (screens, slides, dashboards, documents, logs, code, and meaningful changes over repeated camera frames), preserve observed facts separately from inference, and use configurable temporal chunks plus final synthesis without silent truncation. Configure local/on-premise endpoints, models, timeouts, cloud/egress policy explicitly; never switch providers automatically. This follows the research evidence on interval semantics, Celery idempotency, token budgeting, local privacy, and screen-oriented sampling.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `backend/app/worker.py`, `backend/app/services/{meeting,storage}.py` | Modified | Chain-safe optional stage, statuses, completion timing, original-media retention. |
| `backend/app/services/{visual_segments,ai_agent,meeting_intelligence}.py`, `backend/app/models/base.py`, `backend/app/api/v1/endpoints/meetings.py`, `backend/app/services/template_seed.py` | Modified | Fusion, prompts/templates, summary-only multimodality, backward-compatible contracts. |
| `zabt-vision-worker/zabt_vision/{pipeline,inference}/`, `settings.py` | Modified | Configurable relevance/provider behavior; retain sampled-candidate pipeline. |
| `frontend-2/app/{lib/stage-utils.ts,(dashboard)/meetings/[id]/page.tsx,components/{progress-steps,status-badge,transcript-viewer,sticky-media-player}.tsx}`, `zabt-mobile/lib/stage-utils.ts`, `packages/shared/src/types.ts` | Modified | Preserve screens/viewer/seeking; map new processing statuses. |
| `backend/tests/**`, `zabt-vision-worker/tests/**`, `docs/{configuration.md,self-hosting.md}`, `docker-compose.yml` | Modified | Unit/integration coverage and optional local/private deployment guidance. |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Chain/result or status regressions | High | Contract tests, stable IDs, completion only at chain end. |
| Context/provider limits or visual failure | High | Measured budgets, hierarchical synthesis, bounded warnings, transcript fallback. |
| Sensitive visual/transcript leakage | Med | Existing ownership/storage cleanup; no raw prompts, screenshots, or reasoning in telemetry. |

## Rollback Plan

Disable the optional visual stage and multimodal summary configuration, reverting summary calls to the existing transcript path; then revert the code/config/docs commit. Existing segment rows, APIs, and storage cleanup remain usable.

## Dependencies

- Existing `VisualSegment` persistence and `zabt-vision-worker`; compatible configured vision endpoint/model for deployments that enable video analysis.

## Success Criteria

- [ ] Audio-only, no-video, and vision-failure meetings complete with usable transcript-only summaries and bounded warnings.
- [ ] Enabled video summaries show labeled spoken/visual/uncertainty context with source timestamps/IDs; intelligence remains transcript-only.
- [ ] Tests prove bidirectional overlap, deduplication, chunk-budget behavior without silent truncation, retry idempotency, and status/API/UI compatibility.
