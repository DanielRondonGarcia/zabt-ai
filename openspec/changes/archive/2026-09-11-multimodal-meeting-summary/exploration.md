---
status: explored
executive_summary: >-
  Zabt already persists timestamped transcript and visual segments, but visual
  breakdown is an independent task and the summary/intelligence services consume
  transcript text only. The lowest-risk design is a dynamic, bounded
  temporal-fusion service inserted into the existing Celery flow, with visual
  processing treated as optional and transcript-only fallback preserved.
artifacts:
  - path: openspec/changes/multimodal-meeting-summary/exploration.md
    store: openspec
    type: exploration
next_recommended: sdd-propose
risks:
  - Celery result-shape compatibility
  - Optional-stage failure propagation
  - Long-meeting context limits
  - Media-type detection
  - Provider configuration gaps
  - Evidence/inference separation
  - Backward compatibility with custom templates and existing APIs
skill_resolution:
  mode: paths-injected
  injected_skill_path: 'C:\Users\daniel.rondon\.config\opencode\skills\sdd-explore\SKILL.md'
---

## Exploration: Multimodal Meeting Summary

## Current State

The main Celery pipeline in `backend/app/worker.py` currently runs:

`stage_download -> stage_transcribe -> stage_transliterate -> stage_summarize -> stage_extract_intelligence`

The YouTube pipeline has the same latter stages, but `stage_youtube_download` stores extracted MP3 audio. It does not retain a video object suitable for visual analysis, so YouTube/audio-only processing needs an explicit visual-skip path.

Visual breakdown already exists, but outside the main pipeline:

- `stage_visual_breakdown` is user-triggered from `POST /meetings/{id}/visual-breakdown`.
- It reads the original object-storage file through a one-hour presigned URL, sends transcript hints to `zabt-vision-worker`, and persists `VisualSegment` rows.
- It currently returns a result dictionary, while the existing Celery chain passes `meeting_id` from stage to stage. It also re-raises worker-call exceptions, which would invoke the general `on_stage_failure` handler if inserted unchanged.
- Missing media is currently recorded as visual-breakdown failure (`no_video_file`), whereas the requested integrated behavior is a successful transcript-only fallback.

The data model is already sufficient for a first fusion implementation:

- `TranscriptSegment` contains `start_time`, `end_time`, `speaker`, `text`, and word timing data.
- `VisualSegment` contains `start_time`, `end_time`, `caption`, `confidence`, `sequence`, and `screenshot_s3_key`.
- `VisualSegmentService.get_with_transcript_alignment()` currently attaches transcript lines whose **start time** falls inside each visual interval. That is useful for the existing visual API, but it is not full interval overlap in both directions and should not be reused as the multimodal context algorithm without change.

`stage_summarize` currently passes only `meeting.transcript_text` to `summarize_transcript`, resolves the user/system template, infers a title, marks the meeting completed, and sends notifications. `stage_extract_intelligence` makes two additional full-transcript calls for highlights and meeting-type-specific structured output. No token budgeting, temporal chunking, hierarchical synthesis, or multimodal input path was found.

The built-in templates in `backend/app/services/template_seed.py` already cover executive summaries, topics, decisions, commitments/action items, follow-ups, risks, and open questions. A multimodal implementation should preserve these template contracts and add clearly labeled visual evidence rather than replace the template system.

The vision worker already uses sampled frames, cheap signals, candidate selection, VLM judging, and boundary refinement. It is not designed to send every raw video frame to a VLM. However, the current pipeline has no explicit general-purpose relevance taxonomy for visual findings, no clear face-camera de-emphasis rule, and no persisted distinction between observed visual facts and later inference.

Provider support is incomplete relative to the advertised configuration. `zabt-vision-worker/zabt_vision/settings.py` names Ollama, LM Studio, llama.cpp, and Transformers, but `inference/factory.py` currently implements only Ollama. The current vision defaults and documentation also reference `qwen3-vl:8b-thinking`. The summary client is OpenAI-compatible through `OPENAI_BASE_URL`, but transliteration uses a hardcoded `gpt-4o-mini` call.

The backend `Meeting` contract and frontend stage utilities expose only the existing processing stages. The web and mobile meeting screens expose summary/transcript/structured views; the existing transcript viewer and media player already provide timestamp seeking that can support evidence links without requiring a new viewer in the first slice.

## Affected Areas

- `backend/app/worker.py` — integrate visual analysis and context construction into both processing/resummarization paths; preserve the `meeting_id` Celery chain contract and isolate optional visual failures.
- `backend/app/services/visual_segments.py` — add or support true bidirectional interval-overlap correlation and repeated-visual deduplication without changing the existing visual API semantics unexpectedly.
- `backend/app/services/ai_agent.py` — accept separated spoken and visual context, preserve custom templates, and add bounded long-meeting synthesis.
- `backend/app/services/meeting_intelligence.py` — decide whether highlights and structured outputs consume the same multimodal context or remain transcript-only initially.
- `backend/app/services/meeting.py` — define processing-status transitions and completion timing; `mark_completed()` currently clears `sub_status` during summary before intelligence extraction finishes.
- `backend/app/models/base.py` and `backend/alembic/versions/l0m1n2o3p4q5_add_visual_breakdown.py` — reuse existing segment and visual metadata; only add schema if media-kind or durable fused-context requirements cannot be met from existing data.
- `backend/app/api/v1/endpoints/meetings.py` — preserve current summary and visual endpoints; optionally expose only bounded evidence metadata if the UI needs it, not raw worker output or model reasoning.
- `backend/app/services/storage.py` — preserve original media availability through visual analysis and existing per-meeting visual-prefix cleanup.
- `zabt-vision-worker/zabt_vision/pipeline/run.py`, `video_native.py`, `cross_validate.py`, and `refine_boundaries.py` — retain candidate-frame processing and make relevance/provider behavior configurable rather than introducing a mandatory model/service.
- `zabt-vision-worker/zabt_vision/inference/factory.py` and `settings.py` — reconcile advertised local-provider choices with actual implementations and avoid adding new hardcoded model assumptions.
- `frontend-2/app/lib/stage-utils.ts`, `frontend-2/app/components/ui/progress-steps.tsx`, `frontend-2/app/components/status-badge.tsx`, and `zabt-mobile/lib/stage-utils.ts` — map any new `analyzing_video`/`building_context` statuses without breaking current polling or completed/failed behavior.
- `frontend-2/app/(dashboard)/meetings/[id]/page.tsx`, `frontend-2/app/components/transcript-viewer.tsx`, `frontend-2/app/components/sticky-media-player.tsx`, and `packages/shared/src/types.ts` — retain existing summary editing and playback contracts; add evidence affordances only if required by the selected UI scope.
- `backend/tests/unit/test_visual_segments_service.py`, `backend/tests/unit/test_visual_breakdown_task.py`, `backend/tests/integration/test_visual_breakdown_endpoints.py`, `zabt-vision-worker/tests/`, and new pipeline/summary tests — cover temporal fusion, fallback, provider selection, long meetings, idempotency, and end-to-end chain behavior.
- `backend/app/services/template_seed.py` — verify default and custom template compatibility with separated spoken/visual context.
- `docker-compose.yml`, `zabt-vision-worker/Dockerfile`, `zabt-vision-worker/README.md`, `docs/configuration.md`, and `docs/self-hosting.md` — document optional vision capability, local-only operation, model configuration, and missing provider implementations.

## Approaches

1. **Dynamic temporal fusion at the orchestration boundary** — Keep transcript and visual segments as the source of truth, build a bounded in-memory multimodal context after optional visual analysis, and pass that context to multimodal summary/intelligence services.
   - Pros: no new persistence migration; reuses existing visual worker, storage, segment rows, templates, and APIs; naturally supports transcript-only fallback; avoids storing duplicated sensitive context.
   - Cons: context is rebuilt for resummarization; debugging/replay requires structured logs or a bounded evidence endpoint; long-meeting chunking and Celery task boundaries need explicit design.
   - Effort: Medium

2. **Persist a fused multimodal-context table** — Store normalized time windows containing transcript lines, visual evidence references, provenance, and context-generation metadata before summarization.
   - Pros: easier replay, inspection, caching, auditability, and retrying summary generation without rerunning visual analysis.
   - Cons: new migration and retention/privacy surface; stale-context invalidation after transcript edits or visual reruns; duplicates data already represented by `TranscriptSegment` and `VisualSegment`; larger first implementation.
   - Effort: High

## Recommendation

Choose dynamic temporal fusion for the first implementation.

Add a pure `MultimodalContextBuilder` that reads persisted transcript and visual segments, correlates intervals by actual overlap in both directions, preserves speaker/text/timestamps and caption/confidence/screenshot references, and emits bounded chronological windows. Each visual segment should be referenced once across adjacent transcript windows unless a meaningful visual change warrants repetition.

Integrate the flow as an optional, chain-safe sequence equivalent to:

`download -> transcribe -> transliterate -> visual_breakdown -> build_multimodal_context -> summarize_multimodal -> extract_intelligence`

The visual task must return the chain-compatible `meeting_id` (or be wrapped by a chain-safe task), catch worker/media failures as visual warnings, and allow summarization to continue. Audio-only and YouTube meetings should skip visual analysis rather than report a failed meeting. The original object-storage media must remain available; the transcription temp file is not a valid visual input.

`summarize_multimodal` should preserve the current `MeetingMinutes`/markdown and custom-template contracts while adding clearly separated prompt sections such as `SPOKEN CONTENT`, `VISUAL CONTEXT`, and `INFERENCE/UNCERTAINTY`. Visual captions should be treated as observed evidence; decisions, commitments, owners, and problems must not be invented from ambiguous visuals. The default output can include visual findings and follow-ups, but template-specific sections remain authoritative.

For long meetings, use bounded temporal chunks with partial summaries followed by a final synthesis. Do not silently truncate the transcript or visual evidence. The same bounded context should be made available to structured intelligence only if its schemas and timestamp/provenance behavior are explicitly defined; otherwise preserve transcript-only extraction in the first slice and document the boundary.

Keep the current visual API and storage cleanup behavior. Use existing timestamp seeking for evidence links instead of creating a separate debug UI. Do not expose raw worker output, hidden model reasoning, or screenshots without the existing ownership and signed-URL controls.

Before proposal, resolve media-kind detection for uploaded files: `Meeting` currently stores a path but no explicit MIME/codec field, and extension-only detection is insufficient for all recordings. Also define the status contract (`analyzing_video`, `building_context`, `summarizing`) and whether `completed` moves after intelligence extraction or intentionally remains summary-complete.

## Risks

- **Celery propagation:** `stage_visual_breakdown` currently returns a dictionary and raises on client failure; inserting it unchanged can pass the wrong value to `stage_summarize` or invoke `on_stage_failure`, marking an otherwise usable meeting failed.
- **Optional-stage semantics:** no-video, no-relevant-visual, provider timeout, malformed worker output, and local-provider absence must all preserve a usable transcript summary while retaining bounded visual error telemetry.
- **Long meetings:** current LLM calls receive full transcript text and have no observed token/chunk policy. A 30/60/120-minute recording may exceed provider limits or produce inconsistent summaries without hierarchical synthesis.
- **Evidence quality:** current visual rows contain captions/confidence but no relevance category or explicit observed/inferred provenance. Screen changes, camera faces, cursors, and transient overlays need a conservative relevance policy.
- **Provider/local operation:** advertised LM Studio, llama.cpp, and Transformers vision backends are not implemented in the current factory; the proposal must not silently make Ollama or a hardcoded Qwen model mandatory.
- **Media lifecycle:** visual work needs the original video object, while transcription removes its temporary local audio path. Retention, presigned URL expiry, retries, and deletion must remain safe and idempotent.
- **Status compatibility:** web/mobile progress mapping, polling, summary edit guards, email notifications, and completion semantics all depend on the current status/sub-status behavior.
- **Template/API compatibility:** custom summary templates, summary editing/restoration, PDF/email exports, and shared TypeScript types must continue working with multimodal summaries.
- **Coverage gap:** backend has visual-worker tests but no meaningful tests for the main processing chain, `stage_summarize`, or intelligence extraction. New tests must include both-direction overlap, repeated visual deduplication, fallback, long meetings, and retry behavior.
- **Privacy:** screenshots, transcript text, object keys, and model output are sensitive. Observability should retain counts, statuses, bounded error identifiers, and timing—not raw transcripts, images, prompts, or hidden reasoning.

## Ready for Proposal

Yes. The repository evidence is sufficient to draft a proposal using dynamic temporal fusion with optional visual processing. The proposal should explicitly decide media-kind detection, status/completion semantics, whether structured intelligence is multimodal in the first slice, chunk-size/token limits, provider fallback behavior, and the precise visual-relevance policy. Missing design documentation and incomplete provider implementations should be treated as documented constraints, not inferred capabilities.
