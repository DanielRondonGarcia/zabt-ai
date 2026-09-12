---
schema: gentle-ai.sdd-research/v1
schemaVersion: 1
revision: 3
change: multimodal-meeting-summary
status: done
outcome: done
proposal_ready: true
questions:
  - id: Q1
    text: How should timestamped transcript and visual intervals be correlated, bounded, deduplicated, and exposed with provenance?
  - id: Q2
    text: Which Celery patterns preserve chain compatibility, retries, idempotency, and transcript-only fallback when visual processing is optional?
  - id: Q3
    text: Which established approaches support bounded or hierarchical summarization for long meetings without silent truncation?
  - id: Q4
    text: How should local or on-premise VLM providers be configured, and what privacy boundaries must be explicit?
  - id: Q5
    text: Which conservative visual-filtering practices favor screens, dashboards, logs, code, and documents over irrelevant camera frames?
selected_request:
  source_classes:
    - open-web
  intent:
    - timestamped temporal fusion and provenance
    - optional Celery stages and idempotency
    - bounded or hierarchical long-meeting summarization
    - local VLM configuration and privacy
    - conservative visual filtering
capability_declaration:
  schemaName: gentle-ai.sdd-research-capability
  schemaVersion: 1
  grants:
    documentation: []
    open-web:
      - firecrawl-mcp:public-search-and-scrape
observed_capability:
  schemaName: gentle-ai.sdd-research-capability
  schemaVersion: 1
  grants:
    documentation: []
    open-web:
      - firecrawl-mcp:public-search-and-scrape
admission:
  capability: gentle-ai.sdd-research-capability/v1
  exact_grants:
    documentation: []
    open-web:
      - firecrawl-mcp:public-search-and-scrape
  observed_grants:
    documentation: []
    open-web:
      - firecrawl-mcp:public-search-and-scrape
  admitted_source_class: open-web
  access_policy: Public unauthenticated web pages collected only through Firecrawl search and scrape operations.
  rejected_source_classes:
    - documentation
  blocker: null
---

## Executive Summary

Firecrawl collection admitted the exact user-authorized `open-web` capability and produced auditable public-source evidence for all five selected questions. The evidence supports interval-based temporal fusion, chain-safe optional Celery stages, hierarchical long-document summarization with explicit token accounting, local VLM endpoint configuration with explicit network boundaries, and conservative screen/slide-oriented visual sampling. Repository implementation status and provider availability are not inferred from these sources.

## Capability and Admission

The selected request was re-entered with the following exact capability envelope, and the observed grant matched after successful Firecrawl search and scrape calls:

```json
{
  "schemaName": "gentle-ai.sdd-research-capability",
  "schemaVersion": 1,
  "grants": {
    "documentation": [],
    "open-web": ["firecrawl-mcp:public-search-and-scrape"]
  }
}
```

Only the `open-web` source class was admitted. Repository access, persistence access, generic MCP access, filenames, and prior blocked claims were not used as evidence grants.

## Validated Claims

### Q1 — Temporal overlap, boundaries, deduplication, and provenance

- **Q1.1 — Temporal playback has explicit start and end anchors.** W3C Media Fragments describes temporal fragments as starting playback at the fragment start and pausing at its end. This supports exposing a fused evidence item with a bounded media interval rather than an unbounded timestamp. **Sources:** `W3C-MEDIA-FRAGMENTS-1.0`.
- **Q1.2 — Interval overlap and boundary inclusion must be explicit.** PostgreSQL's range model distinguishes inclusive and exclusive bounds, exposes an overlap operator (`&&`), and uses canonicalization to give equivalent ranges identical representations. This supports an explicit half-open interval convention, overlap testing, and a deterministic normalization key. The half-open choice is an application convention, not a transcript-specific standard requirement. **Sources:** `POSTGRESQL-RANGE-TYPES-17`.
- **Q1.3 — Provenance can be modeled as entities, activities, and agents with generation or derivation relations.** W3C PROV-DM and PROV-O provide a general interchange model for provenance and for describing how data is produced and related across systems and contexts. **Sources:** `W3C-PROV-DM`, `W3C-PROV-O`.

**Design implication:** Build chronological windows from true bidirectional interval overlap, preserve source segment IDs and timestamps, normalize intervals before deduplication, and expose observed visual evidence separately from any generated interpretation. A repeat-visual policy, overlap threshold, and adjacent-window repetition rule remain application decisions.

### Q2 — Optional Celery stages, result propagation, retries, idempotency, and fallback

- **Q2.1 — Celery chains pass results by default.** Celery documents that a chain passes the first task's return value to the next task, while immutable signatures (`.si()` or `immutable=True`) prevent the previous result from being added. Callbacks run after successful completion and errbacks are available for errors. **Sources:** `CELERY-CANVAS-5.6`, `CELERY-CALLING-5.6`.
- **Q2.2 — Task retry is a control-flow operation with bounded retry configuration.** Celery's `Task.retry()` adds the task back to the queue and raises a retry exception to tell the worker that the task was re-sent; `max_retries`, countdown, and retry state are explicit controls. **Sources:** `CELERY-TASK-RETRY-5.6`.
- **Q2.3 — At-least-once execution requires idempotent side effects.** Celery documents that late acknowledgements can cause a task to execute twice after a worker crash, and its canvas guidance recommends idempotent or repeat-tolerant error handlers. **Sources:** `CELERY-TASK-RETRY-5.6`, `CELERY-CANVAS-5.6`.

**Design implication:** An optional visual task should either return the chain's stable meeting identifier or be wrapped by a task with that result contract. It should catch bounded visual/media/provider failures, persist warning telemetry, and return a successful fallback envelope when transcript-only continuation is intended. Celery documents the mechanics, but transcript-only fallback is an application policy and is not defined by Celery itself.

### Q3 — Bounded or hierarchical long-meeting summarization and token budgets

- **Q3.1 — Piecewise summarization controls detail for long inputs.** The OpenAI long-document cookbook explains that long inputs tend to produce summaries that are not proportional to document length, and demonstrates splitting the document into pieces, summarizing piecewise, then controlling detail through the number and size of chunks. **Sources:** `OPENAI-LONG-DOCUMENTS`.
- **Q3.2 — Map/reduce is an established partition-and-synthesis pattern.** The MapReduce paper defines map processing that emits intermediate key/value pairs and reduce merging values associated with the same key; its runtime partitions input and manages execution and failures. This supports hierarchical partial summaries followed by a final synthesis without sending the full meeting in one request. **Sources:** `GOOGLE-MAPREDUCE-2004`.
- **Q3.3 — Token budgets should be measured on the actual request shape.** OpenAI's token-counting guide recommends counting input tokens before sending, reports the exact count the model receives, and notes that formatting, images, files, and tools contribute tokens. **Sources:** `OPENAI-TOKEN-COUNTING`.
- **Q3.4 — Truncation is an explicit behavior, not a safe hidden default.** Transformers documentation distinguishes `truncation=True` from `False`/`do_not_truncate` and allows an explicit `max_length`. **Sources:** `HF-TRANSFORMERS-PADDING-TRUNCATION`.

**Design implication:** Use configurable temporal chunks, reserve output and formatting budget, count text plus visual payloads before each request where the provider supports it, and route over-budget material to another chunk or synthesis level. Do not silently discard transcript or visual intervals; report an explicit bounded failure if a provider cannot accept the planned context.

### Q4 — Local/on-premise VLM endpoint configuration and privacy boundaries

- **Q4.1 — Local servers expose explicit base URLs and model endpoints.** Ollama documents a default local API at `http://localhost:11434/api`; its OpenAI-compatible API uses `http://localhost:11434/v1/` and includes a vision example using `qwen3-vl:8b` with base64 image content. **Sources:** `OLLAMA-API-INTRO`, `OLLAMA-OPENAI-COMPAT-VISION`.
- **Q4.2 — Local endpoint compatibility is also a documented LM Studio pattern.** LM Studio documents serving local models on localhost or a network and switching an OpenAI client base URL to `http://localhost:1234/v1`; its compatibility documentation includes text-and-image chat completions. **Sources:** `LM-STUDIO-SERVER`, `LM-STUDIO-OPENAI-COMPAT`.
- **Q4.3 — Local processing and no-egress are separate deployment assertions.** Ollama states that local prompts and data are not seen by Ollama, but also documents model pulls from the Internet, localhost binding defaults, and a local-only mode via `disable_ollama_cloud` or `OLLAMA_NO_CLOUD=1`. **Sources:** `OLLAMA-FAQ-PRIVACY`.

**Design implication:** Provider configuration should make the base URL, model identifier, vision capability, timeout/retry policy, and network policy explicit. A no-egress deployment needs local-only settings plus deployment-level firewall, DNS, proxy, and preloaded-model controls; a localhost URL alone is not proof of no egress. No automatic provider switching is justified by these sources.

### Q5 — Conservative visual relevance filtering

- **Q5.1 — Screen-share content can be more information-dense than camera footage for meeting understanding.** The Visual MEMENTO abstract identifies slides, charts, dashboards, and live demos in screen-share sessions as information-dense content and describes temporal segmentation, multimodal fusion, and anomaly filtering. **Sources:** `VISUAL-MEMENTO-2026`.
- **Q5.2 — Presentation-video systems segment slide intervals and combine visual content with speech.** PreMind describes VLM-assisted segmentation into slide-presentation segments, slide visual extraction, speech transcription, and consolidation into integrated understanding; it specifically notes details visible only on slides. **Sources:** `PREMIND-2025`.
- **Q5.3 — Conservative sampling can combine shot boundaries, representative candidates, and redundancy elimination.** LMSKE describes shot segmentation, per-shot candidate selection, and redundancy elimination while preserving shot order; a separate scene-detection study describes adaptive segmentation and lightweight scores using sharpness, luminance, and temporal spread. **Sources:** `LMSKE-2024`, `SCENE-DETECTION-2025`.

**Design implication:** Start with a screen/slide/dashboard/document-first prior, retain error and change candidates, suppress repeated near-identical frames, preserve temporal coverage, and use a confidence threshold that allows transcript-only continuation. The sources do not prescribe the exact Zabt taxonomy for logs, code, errors, or face-camera de-emphasis; those weights and labels remain product decisions and must be tested against representative meetings.

## Evidence-to-Design Boundary

The public sources establish reusable patterns and constraints. They do not prove that any provider, endpoint, task, or privacy control is implemented in this repository. The following decisions remain confirmed, non-authoritative product inputs supplied by exploration and the user:

- Summary-only multimodality in the first slice; intelligence remains transcript-only.
- Conservative original-media MIME/codec or `ffprobe` detection without a new database column.
- `completed` after the full processing chain.
- Configurable hierarchical chunks and final synthesis for long meetings.
- No automatic provider switching; bounded vision failure with transcript-only fallback.
- Dynamic relevance/provenance without migration.
- Backend-first exposure and reuse of the existing viewer/timestamp seeking.

## Contradictions, Uncertainty, and Freshness

- W3C Media Fragments provides temporal playback semantics, not a complete transcript/visual matching algorithm. PostgreSQL range semantics are a useful formal analogy and implementation pattern, not a meeting-data standard.
- Celery documents result propagation, error callbacks, retries, and at-least-once execution concerns, but it does not define the domain meaning of an optional visual warning or transcript-only success.
- Token limits, image accounting, model context windows, and provider behavior vary. The OpenAI token-counting guidance is provider-specific; the safe cross-provider rule is to budget explicitly and avoid implicit truncation.
- Ollama's local privacy statement coexists with documented Internet model pulls and cloud features. No-egress must therefore be enforced and audited at deployment boundaries rather than inferred from a local base URL.
- Visual MEMENTO is a 2026 Springer conference-paper abstract with preview-only access; PreMind, LMSKE, and the scene-detection study are arXiv sources or preprints. They support patterns, not universal relevance weights.
- No source directly establishes that code, logs, or error frames always outrank faces in every meeting. The requested priority is retained as a conservative product policy and remains a validation target.
- Several exploratory Firecrawl searches returned no result or one attempted Apache Beam URL returned 404; those attempts produced no claims and are not included in the source set. All admitted claims below come from successful Firecrawl search or scrape results.
- Revision 2 was blocked with empty exact and observed grants. Its invalidated source state was replaced; no prior external claim was reused.

## Sources

All sources have class `open-web`, were accessed on 2026-09-10, and were collected with Firecrawl search or scrape. Source IDs are stable artifact-local identifiers; they are not claims of repository implementation.

### `W3C-MEDIA-FRAGMENTS-1.0`

- class: `open-web`
- title: Media Fragments URI 1.0 (basic)
- publisher: World Wide Web Consortium (W3C)
- URL: https://www.w3.org/TR/media-frags/
- accessed_at: 2026-09-10
- excerpt: "For a temporal URI fragment, it is recommended to start playback at a time offset that equals to the start of the fragment and pause at the end of the fragment."

### `POSTGRESQL-RANGE-TYPES-17`

- class: `open-web`
- title: PostgreSQL 17 — 8.17. Range Types
- publisher: PostgreSQL Global Development Group
- URL: https://www.postgresql.org/docs/17/rangetypes.html
- accessed_at: 2026-09-10
- excerpt: "Every non-empty range has two bounds" with inclusive or exclusive endpoints; the examples use `&&` for overlaps, and canonicalization can map equivalent ranges to identical representations.

### `W3C-PROV-DM`

- class: `open-web`
- title: PROV-DM: The PROV Data Model
- publisher: World Wide Web Consortium (W3C)
- URL: https://www.w3.org/TR/prov-dm/
- accessed_at: 2026-09-10
- excerpt: "Provenance is information about entities, activities, and people involved in producing a piece of data or thing," useful for assessments of quality, reliability, or trustworthiness.

### `W3C-PROV-O`

- class: `open-web`
- title: PROV-O: The PROV Ontology
- publisher: World Wide Web Consortium (W3C)
- URL: https://www.w3.org/TR/prov-o/
- accessed_at: 2026-09-10
- excerpt: PROV-O expresses the PROV Data Model in OWL2 and provides classes, properties, and restrictions to represent and interchange provenance generated in different systems and contexts.

### `CELERY-CANVAS-5.6`

- class: `open-web`
- title: Canvas: Designing Work-flows — Celery 5.6.3 documentation
- publisher: Celery Project
- URL: https://docs.celeryq.dev/en/stable/userguide/canvas.html
- accessed_at: 2026-09-10
- excerpt: "The first task executes passing its return value to the next task in the chain, and so on." The same page documents immutable signatures, success callbacks, error callbacks, and repeat-tolerant errbacks.

### `CELERY-CALLING-5.6`

- class: `open-web`
- title: Calling Tasks — Celery 5.6.3 documentation
- publisher: Celery Project
- URL: https://docs.celeryq.dev/en/stable/userguide/calling.html
- accessed_at: 2026-09-10
- excerpt: Celery applies callbacks with the parent result, supports errbacks for exceptions, and exposes retry policies with a maximum retry count and retryable error selection.

### `CELERY-TASK-RETRY-5.6`

- class: `open-web`
- title: celery.app.task — Celery 5.6.3 documentation
- publisher: Celery Project
- URL: https://docs.celeryq.dev/en/stable/reference/celery.app.task.html#celery.app.task.Task.retry
- accessed_at: 2026-09-10
- excerpt: "Retry the task, adding it to the back of the queue." `retry()` raises a retry exception for the worker, and `max_retries`, countdown, and task acknowledgement behavior are explicit controls.

### `OPENAI-LONG-DOCUMENTS`

- class: `open-web`
- title: Summarizing Long Documents
- publisher: OpenAI Developers
- URL: https://developers.openai.com/cookbook/examples/summarizing_long_documents.md
- accessed_at: 2026-09-10
- excerpt: Long-document summaries are not proportional to input length; the recipe splits a document into pieces, summarizes piecewise, and controls detail through the number and size of chunks.

### `GOOGLE-MAPREDUCE-2004`

- class: `open-web`
- title: MapReduce: Simplified Data Processing on Large Clusters
- publisher: Google Research
- URL: https://research.google/pubs/mapreduce-simplified-data-processing-on-large-clusters/
- accessed_at: 2026-09-10
- excerpt: Map processes key/value pairs into intermediate pairs and reduce merges values for the same key; the runtime partitions input, schedules execution, and handles machine failures.

### `OPENAI-TOKEN-COUNTING`

- class: `open-web`
- title: Counting tokens — OpenAI API
- publisher: OpenAI Developers
- URL: https://developers.openai.com/api/docs/guides/token-counting.md
- accessed_at: 2026-09-10
- excerpt: Token counting determines input tokens before sending, helps fit context limits, and returns the exact count for text, images, files, tools, formatting, and conversations.

### `HF-TRANSFORMERS-PADDING-TRUNCATION`

- class: `open-web`
- title: Padding and truncation
- publisher: Hugging Face Transformers
- URL: https://huggingface.co/docs/transformers/main/en/pad_truncation
- accessed_at: 2026-09-10
- excerpt: `truncation=True` removes tokens to a model or explicit `max_length`, while `False` or `do_not_truncate` applies no truncation.

### `OLLAMA-API-INTRO`

- class: `open-web`
- title: Introduction — Ollama API
- publisher: Ollama
- URL: https://docs.ollama.com/api/introduction
- accessed_at: 2026-09-10
- excerpt: After installation, Ollama serves its API by default at `http://localhost:11434/api`; the page also shows a local `curl` request to `/api/generate`.

### `OLLAMA-OPENAI-COMPAT-VISION`

- class: `open-web`
- title: OpenAI compatibility — Ollama
- publisher: Ollama
- URL: https://docs.ollama.com/api/openai-compatibility
- accessed_at: 2026-09-10
- excerpt: The OpenAI client can use `base_url='http://localhost:11434/v1/'`; the page documents `/v1/chat/completions` vision with `qwen3-vl:8b` and base64 image content.

### `OLLAMA-FAQ-PRIVACY`

- class: `open-web`
- title: FAQ — Ollama
- publisher: Ollama
- URL: https://docs.ollama.com/faq
- accessed_at: 2026-09-10
- excerpt: Ollama says local prompts and data are not seen by Ollama, documents default `127.0.0.1` binding, and provides `disable_ollama_cloud` or `OLLAMA_NO_CLOUD=1`; it also notes that model pulls use the Internet.

### `LM-STUDIO-SERVER`

- class: `open-web`
- title: LM Studio as a Local LLM API Server
- publisher: LM Studio
- URL: https://lmstudio.ai/docs/developer/core/server
- accessed_at: 2026-09-10
- excerpt: LM Studio can serve local models on localhost or a network through REST, SDK, OpenAI-compatible, and Anthropic-compatible endpoints.

### `LM-STUDIO-OPENAI-COMPAT`

- class: `open-web`
- title: OpenAI Compatibility Endpoints
- publisher: LM Studio
- URL: https://lmstudio.ai/docs/developer/openai-compat
- accessed_at: 2026-09-10
- excerpt: Existing OpenAI clients can switch their base URL to `http://localhost:1234/v1`; supported endpoints include chat completions with text and images.

### `VISUAL-MEMENTO-2026`

- class: `open-web`
- title: Visual MEMENTO: A Foundation Model Framework for Recording, Understanding, and Retrieving Screen-Share Sessions in Meetings
- publisher: Springer Nature
- URL: https://link.springer.com/chapter/10.1007/978-3-032-29558-3_8
- accessed_at: 2026-09-10
- excerpt: The abstract calls slides, charts, dashboards, and live demos in screen-share sessions information-dense, and describes temporal segmentation, multimodal fusion, and anomaly filtering.

### `PREMIND-2025`

- class: `open-web`
- title: PreMind: Multi-Agent Video Understanding for Advanced Indexing of Presentation-style Videos
- publisher: arXiv
- URL: https://arxiv.org/abs/2503.00162
- accessed_at: 2026-09-10
- excerpt: PreMind segments videos into slide-presentation segments, extracts slide visual content, transcribes speech, and consolidates both for integrated understanding of details visible only on slides.

### `LMSKE-2024`

- class: `open-web`
- title: Large Model based Sequential Keyframe Extraction for Video Summarization
- publisher: arXiv
- URL: https://arxiv.org/abs/2401.04962
- accessed_at: 2026-09-10
- excerpt: The method cuts video into shots, clusters candidate frames, removes redundancy within each shot, and concatenates selected frames in shot order.

### `SCENE-DETECTION-2025`

- class: `open-web`
- title: Scene Detection Policies and Keyframe Extraction Strategies for Large-Scale Video Analysis
- publisher: arXiv
- URL: https://arxiv.org/abs/2506.00667
- accessed_at: 2026-09-10
- excerpt: The abstract describes adaptive segmentation by video length and a lightweight keyframe score using sharpness, luminance, and temporal spread to support relevance and efficient processing.

## Key Learnings

1. Explicit half-open interval conventions make timestamp overlap and evidence seeking deterministic.
2. Optional Celery stages must preserve chain result shape while treating retries and duplicate execution as normal possibilities.
3. Long-meeting summarization needs measured token budgets, hierarchical chunks, and visible over-budget behavior.
4. Local VLM privacy requires endpoint configuration plus explicit cloud, proxy, and network-egress controls.
5. Screen-share relevance is supported by temporal segmentation and redundancy elimination, while exact meeting taxonomy weights remain product policy.
