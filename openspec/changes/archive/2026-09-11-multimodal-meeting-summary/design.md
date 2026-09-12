# Design: Multimodal Meeting Summary

## Technical Approach

Add `stage_optional_visual_breakdown` between `stage_transliterate` and `stage_summarize` in `backend/app/worker.py`. It returns only `meeting_id`; summary rebuilds ephemeral context from persisted `TranscriptSegment` and `VisualSegment` rows. Synthesis is hierarchical/multimodal; intelligence remains transcript-only and finalizes `completed`. Preserve existing APIs, signed URLs, templates, edit/restore/export, and seeking.

## Architecture Decisions

| Decision | Choice | Rejected | Rationale |
|---|---|---|---|
| Context | Pure typed chunks; no fused table | Persist fused context | Avoids migration, duplication, and stale invalidation. |
| Chain | Optional stage always returns the stable ID | Pass worker dictionaries through Celery | Skips cannot corrupt result propagation. |
| Eligibility | YouTube short-circuit; MIME/codec metadata, then bounded `ffprobe` | Extension-only detection | Preserves conservative original-media skips. |
| Summary | Timestamped captions plus spoken evidence; map partials, then final synthesis | One full request | Makes token limits explicit. |

Provider/privacy configuration is an explicit contract:

```text
VISION_ENABLED=false; VISION_BACKEND=local|runpod; VISION_LOCAL_URL=http://zabt-vision-worker:8003; POST /run
VISION_RUNPOD_ENDPOINT_ID=; VISION_RUNPOD_API_KEY=; VISION_JUDGE_MODEL=qwen3-vl:8b-thinking; VISION_REQUIRE_VISION=true
VISION_TIMEOUT=1800; VISION_MAX_RETRIES=2; VISION_RETRY_BACKOFF_SECONDS=5
VISION_CLOUD_ALLOWED=false; VISION_EGRESS_POLICY=deny|allowlist; VISION_ALLOWED_HOSTS=
VISION_INFERENCE_BACKEND=ollama; OLLAMA_HOST=...; OLLAMA_NO_CLOUD=1
SUMMARY_CHUNK_SECONDS=120; SUMMARY_MAX_INPUT_TOKENS=6000
```

RunPod requires explicit cloud allowance; `OPENAI_BASE_URL`/`OPENAI_MODEL` remain the summary provider. Failure takes bounded fallback; no automatic switching.

## Data Flow

```text
download → transcribe → transliterate → optional_visual
  → summarize(build_context) → intelligence(transcript-only) → finalize
```

The visual stage retains original media, skips YouTube/audio-only input, and converts media/provider/malformed-output failures to `VisualStageOutcome` fallback without failing the meeting. It sets `analyzing_video`; `stage_summarize` sets `building_context`, then `summarizing`; final intelligence clears status. A locked queue transaction stores `run_epoch` in existing `visual_breakdown_params`; key: `visual-breakdown:{meeting_id}:{run_epoch}`. Redis `SET NX EX` leases are reused by retries; duplicates/terminal deliveries return the ID. Atomic segment replacement, outcome, and persisted one-time markers prevent duplicate warnings/telemetry/notifications; reruns get a new epoch. Telemetry has only counts, timing, status, and opaque errors.

## File Changes

| File | Action | Description |
|---|---|---|
| `backend/app/worker.py` | Modify | Integrate optional stage, retries, statuses, and finalization. |
| `backend/app/services/multimodal_context.py` | Create | Overlap, relevance, deduplication, provenance, budgeting, and chunks. |
| `backend/app/services/{ai_agent.py,template_seed.py,meeting_intelligence.py,meeting.py,visual_segments.py}` | Modify | Labeled hierarchical prompts, transcript-only intelligence, and convergence. |
| `backend/app/services/visual_breakdown/{vision_client.py,types.py}`, `backend/app/services/{storage.py}`, `backend/app/core/config.py` | Modify | Typed fallback, media metadata, and provider policy. |
| `zabt-vision-worker/zabt_vision/{pipeline/run.py,pipeline/video_native.py,types.py,settings.py,inference/factory.py}` | Modify | Safe probe, sampled candidates, and explicit capability/provider validation. |
| `backend/app/api/v1/endpoints/meetings.py`, `backend/app/models/base.py`, `packages/shared/src/{types.ts,enums.ts}`, `frontend-2/app/lib/{api.ts,stage-utils.ts}`, `frontend-2/app/components/{ui/progress-steps.tsx,status-badge.tsx,transcript-viewer.tsx,sticky-media-player.tsx}`, `frontend-2/app/(dashboard)/meetings/[id]/page.tsx`, `zabt-mobile/lib/stage-utils.ts` | Modify/verify | Preserve contracts and existing viewer behavior; no migration. |
| `backend/tests/unit/test_multimodal_context.py`, `backend/tests/unit/{test_visual_breakdown_task.py,test_vision_client.py}`, `backend/tests/contract/test_meetings.py`, `zabt-vision-worker/tests/**`, `docs/{configuration.md,self-hosting.md}`, `docker-compose.yml` | Create/modify | RED/regression tests and private/local deployment guidance. |

## Interfaces / Contracts

```python
# All four are frozen dataclasses; fields are shown compactly.
class ContextItem: source:Literal["spoken","visual"];source_id:int;start:float;end:float;content:str;relevance:Literal["high","medium","low"];provenance:Literal["observed","inferred"];uncertainty:str|None;fragment_index:int;speaker:str|None;confidence:float|None;evidence_ref:str|None
class ContextChunk: index:int;start:float;end:float;items:tuple[ContextItem,...];estimated_input_tokens:int;complete:bool
class ContextBuildResult: chunks:tuple[ContextChunk,...];source_item_count:int;assigned_item_count:int;unassigned_items:tuple[ContextItem,...];completeness:Literal["complete","incomplete"];warning_codes:tuple[str,...]
class VisualStageOutcome: status:Literal["completed","skipped","fallback"];reason:str|None;warning_code:str|None;segment_count:int;idempotency_key:str;attempts:int
```

The builder uses true overlap `spoken.start < visual.end and visual.start < spoken.end`; touching `[start,end)` boundaries are excluded. Relevance ranks screens, slides, dashboards, documents, logs, code, and errors above repeated camera frames. Prompts label `SPOKEN CONTENT`, `VISUAL CONTEXT`, and `INFERENCE/UNCERTAINTY` with source IDs and bounded timestamps. Whole items roll to the next chunk; oversized spoken items split on word/sentence boundaries while retaining ID, timestamps, and `fragment_index`. `unassigned_items`, `completeness`, and warnings expose overflow; provider truncation and silent loss are forbidden.

## Testing Strategy

| Layer | Planned RED coverage |
|---|---|
| Unit | Enclosing/touching intervals, deduplication, provenance, labels, templates, and measured budgets. |
| Integration | ID propagation, lease convergence, skip/fallback, HTTP timeout/non-2xx/malformed output, probe failure, retention, and completion ordering. |
| Contract/UI | Existing API/edit/export tests plus web/mobile status mapping and timestamp seeking. |

## Threat Matrix

| Boundary | Applicability/behavior | Planned RED tests |
|---|---|---|
| Documentation-like paths: `requirements.txt`, `CMakeLists.txt`, executable Markdown/MDX, `README.sh` | N/A — no executable classification. | None |
| Git repository selection: `git -C`, relative paths, absolute paths | N/A — no Git command. | None |
| Commit state: staged, `commit -a`, empty index | N/A — no commit automation. | None |
| Push state: tracking branch, first push, explicit refspec | N/A — no push automation. | None |
| PR commands: explicit `--head`, environment prefix, composed commands | N/A — no PR automation. | None |
| Process integration: Celery retries/results; HTTP `POST /run`; `ffprobe` nonzero/invalid input | Applicable — preserve ID; bounded retries/validation, sanitized warnings, fresh signed URLs, argv-only, no provider switch. Timeout/non-2xx/malformed/invalid media skips/falls back; missing meeting/DB is fatal. | Duplicate/retry; timeout/4xx/malformed/no-secret; audio-only/nonzero/invalid probe. |

## Out of Scope

No fused context, migration, or media-kind column; no multimodal intelligence, broad UI redesign, new viewer, mandatory external provider, automatic switching, all-frame VLM analysis, or reasoning exposure.

## Migration / Rollout

No migration. Ship disabled; validate local/private egress controls, then enable per deployment. Disabling restores transcript-only summaries.

## Open Questions

- [ ] Which conservative tokenizer estimator should each summary provider use?
- [ ] Should progress show a separate visual step or one mapped label?
