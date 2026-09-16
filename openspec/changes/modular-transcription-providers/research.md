---
schema: gentle-ai.sdd-research/v1
schemaVersion: 1
revision: 4
change: modular-transcription-providers
status: success
outcome: done
proposal_ready: false
questions:
  - id: Q1
    text: Establish the official batch/file model identifiers and the documented Python and transcription API contract.
  - id: Q2
    text: Select a first conformance model candidate and a configurable comparison candidate using only the admitted capability and observed pricing evidence.
  - id: Q3
    text: Bound timestamps, duration, language controls, diarization, response formats, and supported file formats for the first batch slice.
  - id: Q4
    text: Keep batch/file transcription and realtime transcription as separate capability contracts.
  - id: Q5
    text: Record the official Ollama compatibility boundary without inferring audio support from chat compatibility.
  - id: Q6
    text: Define a source-independent conformance fixture and acceptance matrix for the first batch implementation slice.
selected_request:
  source_classes:
    - documentation
    - open-web
  intent:
    - official batch/file model identifiers and API contract
    - first conformance candidate and configurable comparison candidate
    - timestamps, duration, language, diarization boundaries, file formats, and observed pricing
    - separate batch versus realtime capability boundary
    - official Ollama compatibility boundary
    - first-slice conformance fixture and acceptance matrix
  deferred_non_blocking_questions:
    - exact retry and cancellation semantics
    - full privacy and retention policy
    - medical transcription parity and handling
    - generic OpenAI-compatible base_url audio behavior
    - production quality and latency benchmarking
  scope_decisions:
    - Realtime is a separate optional capability and is excluded from the first batch implementation slice.
    - Speaker policy is not inferred; SPEAKER_UNKNOWN and diarization placement remain orchestrator decisions.
    - Medical handling is not inferred and remains an orchestrator decision.
  screenshot_context:
    labels:
      - GPT-Transcribe
      - GPT-Live-Transcribe
      - GPT-Realtime-Whisper
      - GPT-4o Transcribe
      - GPT-4o Mini Transcribe
    authority: non-authoritative user context; do not promote labels to evidence
capability_declaration:
  schemaName: gentle-ai.sdd-research-capability
  schemaVersion: 1
  grants:
    - documentation
    - open-web
  sourceIds:
    documentation:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
    open-web:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
observed_capability:
  schemaName: gentle-ai.sdd-research-capability
  schemaVersion: 1
  grants:
    - documentation
    - open-web
  sourceIds:
    documentation:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
    open-web:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
admission:
  capability: gentle-ai.sdd-research-capability/v1
  requested_grants:
    documentation:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
    open-web:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
  exact_grants:
    documentation:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
    open-web:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
  observed_grants:
    documentation:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
    open-web:
      - firecrawl:01a0a192-b123-771c-92c8-70290e23599b
      - firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc
      - firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00
      - firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd
      - firecrawl:01a0a189-a6f8-7325-867e-f305964fde69
      - firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8
      - firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049
  admitted_source_class:
    - documentation
    - open-web
  access_policy: User-authorized Firecrawl first-party source IDs only.
  rejected_source_classes: []
  blocker: null
product_recommendations:
  - First conformance candidate: gpt-transcribe, because the admitted model page describes high-accuracy speech-to-text, a transcription endpoint, audio input/text output, and an observed price of $0.0045 per minute.
  - Configurable comparison candidate: gpt-4o-mini-transcribe, because the admitted model page describes a high-performance, fast model and publishes a distinct audio-token price, rate-limit table, and snapshots.
  - Keep gpt-4o-transcribe-diarize as an optional speaker-capability candidate only after the orchestrator decides whether speaker data is required and where diarization belongs.
  - Treat gpt-transcribe and gpt-4o-mini-transcribe pricing as non-comparable until units are normalized; do not infer a production cost winner from the observed listings.
product_decisions:
  status: pending
  items:
    - Decide whether SPEAKER_UNKNOWN is acceptable for the first batch slice.
    - Decide whether diarization belongs in the cloud transcription call or in a local/RunPod post-processing stage.
    - Decide how the existing medical transcription path is handled; this research does not establish medical parity.
    - Confirm the production default after the conformance comparison; the candidates above are not a final production selection.
skill_resolution:
  mode: paths-injected
  injected_skill_paths:
    - C:\Users\daniel.rondon\.config\opencode\skills\sdd-research\SKILL.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\research-lifecycle.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\sdd-phase-common.md
    - C:\Users\daniel.rondon\.config\opencode\skills\_shared\openspec-convention.md
---

## Executive Summary

The newly supplied first-party Firecrawl declaration is admitted exactly for `documentation` and `open-web`, and the selected request is narrowed to the first batch implementation slice. The admitted sources establish the file-model IDs, Python/API boundary, formats, response and timestamp constraints, observed prices, the separate realtime boundary, and the official Ollama compatibility boundary. Research is complete for this lane, but proposal readiness remains false because speaker/diarization, medical handling, and final production selection are orchestrator-owned pending decisions.

## Selected Intent and Capability Admission

This revision retains only the first-slice request listed in the front matter. The following questions are explicitly deferred and are not answered or asserted here: exact retry and cancellation semantics, full privacy and retention policy, medical transcription parity, generic OpenAI-compatible `base_url` audio behavior, and production quality and latency benchmarking. Realtime is recorded as a separate optional capability and is not part of the first batch implementation.

Only the seven exact Firecrawl IDs in the admitted declaration are used for evidence claims below. Repository files, the previous partial revision, screenshot labels, memory, generic tools, and persistence access are not evidence grants.

## Validated Claims

### Q1 — Official batch/file identifiers and API contract

- **Q1.1 — The official file-model list includes `gpt-transcribe`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, dated `gpt-4o-mini-transcribe-2025-12-15`, `whisper-1`, and `gpt-4o-transcribe-diarize`.** The official Python reference lists these identifiers for file transcription. **Source:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`.
- **Q1.2 — The documented Python operation is `audio.transcriptions.create`.** The reference documents a file object, model, response format, language, chunking, timestamps, and related transcription controls. **Source:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`.
- **Q1.3 — The transcription API documents JSON, diarized JSON, verbose JSON, and transcript-event stream response forms, with model-specific response and timestamp constraints.** **Source:** `firecrawl:01a0a189-a6f8-7325-867e-f305964fde69`.
- **Q1.4 — The first adapter should target the documented file-transcription operation rather than infer an audio contract from the existing chat or vision configuration.** This is an implementation boundary derived from the official file operation and response contract; it is not a claim about arbitrary compatible endpoints. **Sources:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`, `firecrawl:01a0a189-a6f8-7325-867e-f305964fde69`.

### Q2 — First conformance and comparison candidates

- **Q2.1 — `gpt-transcribe` is the first conformance candidate for the batch slice.** Its admitted model page describes a high-accuracy speech-to-text model for completed files, streamed file transcripts, and committed Realtime turns; it lists audio input, text output, a transcription endpoint, streaming support, keyword/context controls, and a price of `$0.0045` per minute. It also states that function calling and structured outputs are unavailable and that a rate-limit table is published. **Source:** `firecrawl:01a0a192-b123-771c-92c8-70290e23599b`.
- **Q2.2 — `gpt-4o-mini-transcribe` is the configurable comparison candidate.** Its admitted model page describes a high-performance, fast model, lists audio input and text output through the transcription endpoint, publishes `$1.25` input and `$5` output per 1M audio tokens, and publishes rate-limit and snapshot information. **Source:** `firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc`.
- **Q2.3 — `gpt-4o-transcribe-diarize` is an optional speaker-capability candidate, not an unapproved default.** Its admitted model page documents built-in speaker diarization, says it is available only in the Transcription API, and lists `$2.50` input and `$10` output per 1M audio tokens plus a rate-limit table. **Source:** `firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00`.
- **Q2.4 — The observed prices use different units and must not be ranked directly.** `gpt-transcribe` is listed per minute, while the two token-priced model pages use input/output audio-token units. The conformance fixture should record the provider's unit and observed value without claiming a normalized cost comparison. **Sources:** `firecrawl:01a0a192-b123-771c-92c8-70290e23599b`, `firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc`, `firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00`.

### Q3 — Output, timestamp, language, speaker, duration, and format boundaries

- **Q3.1 — The documented input formats are `flac`, `mp3`, `mp4`, `mpeg`, `mpga`, `m4a`, `ogg`, `wav`, and `webm`.** **Source:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`.
- **Q3.2 — Language is an explicit transcription request parameter, and `gpt-transcribe` supports unstructured context, keyword hints, and multiple language hints.** The exact language catalog and Zabt-language mapping are not asserted by this revision. **Sources:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`, `firecrawl:01a0a192-b123-771c-92c8-70290e23599b`.
- **Q3.3 — `verbose_json` supports `word` or `segment` timestamp granularities.** The same Python reference states that timestamp granularities are unavailable for `gpt-4o-transcribe-diarize`. **Source:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`.
- **Q3.4 — Diarized JSON provides speaker segments and duration for `gpt-4o-transcribe-diarize`.** The admitted evidence does not establish word-level speaker labels, and no such labels may be fabricated during normalization. **Source:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`.
- **Q3.5 — The general first-slice result must treat words, speaker labels, and duration as capability-dependent fields.** Segment and word timing are documented only through the stated response/timestamp boundaries; diarized output has its own constraint. This is a normalization rule derived from the documented model-specific contract, not a claim that every model returns every field. **Sources:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`, `firecrawl:01a0a189-a6f8-7325-867e-f305964fde69`.

### Q4 — Batch/file versus realtime capability

- **Q4.1 — File transcription and realtime transcription are separate API concerns.** The Python file reference and transcription API reference describe file input and transcription response forms, while the Realtime reference describes separate realtime session/transcription behavior. **Sources:** `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`, `firecrawl:01a0a189-a6f8-7325-867e-f305964fde69`, `firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8`.
- **Q4.2 — `gpt-transcribe` being listed with streaming support does not remove the separate realtime capability boundary.** The model page mentions streamed file transcripts and committed Realtime turns, while the official Realtime reference documents its own session behavior. The first batch adapter must not be treated as a replacement for a realtime chunk provider. **Sources:** `firecrawl:01a0a192-b123-771c-92c8-70290e23599b`, `firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8`.
- **Q4.3 — Realtime is excluded from the first batch implementation slice.** This is the approved scope decision for this research revision, not an external provider claim. A future realtime adapter requires its own contract and conformance tests.

### Q5 — Official Ollama compatibility boundary

- **Q5.1 — Ollama's official OpenAI-compatibility reference documents chat completions, responses, vision, tools, embeddings, and streaming, but its supported endpoint list does not document `/v1/audio/transcriptions`.** **Source:** `firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049`.
- **Q5.2 — Ollama speech-to-text is not eligible for the first audio registry through the admitted compatibility surface.** This is a conservative registry eligibility rule based on the documented endpoint list; it does not claim that no separate or future Ollama audio API can exist. **Source:** `firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049`.

## Proposed First-Slice Conformance Fixture

The following is a test/product proposal, not external evidence:

- Use one short, known batch recording of approximately 60–90 seconds with a declared language and a transcript oracle.
- Keep a clean WAV copy and an MP3 copy; use the other documented formats as explicit format-coverage cases rather than silently assuming conversion support.
- Include two speakers and a short turn-taking or overlap section so speaker behavior is observable without making a speaker policy implicit.
- Record expected normalized fields: transcript text, requested and observed language, segments, words when returned, speakers or `SPEAKER_UNKNOWN`, duration when returned, provider/model metadata, response format, observed usage/cost with its original unit, and normalized errors.
- Run the same fixture against `gpt-transcribe` and the configured `gpt-4o-mini-transcribe` comparison model.
- Keep `gpt-4o-transcribe-diarize` as an opt-in speaker-capability case after the orchestrator decides whether speaker data is required.
- Do not include medical parity, generic `base_url` behavior, privacy/retention validation, or production benchmarking in the first conformance fixture; those are explicitly deferred.

## First-Slice Acceptance Matrix

| Capability | First-slice requirement | Evidence disposition | Acceptance check |
|---|---|---|---|
| Canonical batch model ID | Required | `gpt-transcribe` is the first candidate; `gpt-4o-mini-transcribe` is the configurable comparison | The adapter sends the configured canonical ID and records it in provider metadata; no silent fallback is allowed |
| File input | Required | `audio.transcriptions.create` accepts a file object | The adapter submits the fixture as a file-based request rather than assuming a URL-only contract |
| File formats | Required | `flac`, `mp3`, `mp4`, `mpeg`, `mpga`, `m4a`, `ogg`, `wav`, and `webm` are listed | WAV and MP3 fixtures pass; each additional format is either exercised or explicitly marked untested |
| Language control | Required | `language` is documented; exact catalog mapping remains a conformance check | The declared language is sent, and returned language behavior is recorded without inventing a mapping |
| Transcript text | Required | Audio input/text output is documented for the candidate model pages | Normalized text is compared with the fixture oracle using an agreed product threshold |
| Segment timestamps | Required when downstream seeking needs them | `verbose_json` segment granularity is documented | Segment start/end values are present, ordered, and non-negative when the selected response format supports them |
| Word timestamps | Capability-dependent | `verbose_json` word granularity is documented; diarized model has a timestamp-granularity restriction | Preserve absent words or timings as absent; never synthesize them |
| Speaker output | Pending product decision | Built-in speaker diarization and speaker segments are documented only for the diarized model | Apply either the approved `SPEAKER_UNKNOWN` policy or the approved explicit diarization stage |
| Duration | Required for meeting persistence when returned | Duration is documented for diarized JSON; general behavior is not generalized here | Prefer a returned duration; otherwise preserve unknown rather than fabricate media duration |
| Response format | Required | JSON, diarized JSON, verbose JSON, and transcript-event streams are documented with model-specific constraints | Decode only a supported format for the selected model and fail with a normalized capability error for invalid combinations |
| Provider metadata | Required | Model pages and API references establish model/endpoint identity | Persist provider, canonical model ID, response format, and observed usage/cost unit |
| Observed pricing | Required for comparison record | `$0.0045/minute` for `gpt-transcribe`; token-unit listings for the other admitted models | Record the listing and measured usage in original units; do not declare a normalized winner |
| Batch versus realtime | Batch required; realtime excluded | Separate file and realtime references | Batch tests use the file API; realtime is not exposed through the first batch provider contract |
| Ollama compatibility | Not eligible by default | `/v1/audio/transcriptions` is not documented on the admitted Ollama compatibility page | Do not register Ollama as an audio provider without a separate first-party audio contract |

## Deferred Non-Blocking Questions

These are intentionally deferred follow-up questions. This revision makes no claim about their answers and does not use them to block completion of the narrowed research lane:

1. What exact retry and cancellation semantics, including bounded timeout behavior, should the adapter implement?
2. What full privacy, retention, and data-use policy applies to the selected deployment?
3. Does the cloud candidate provide acceptable medical transcription parity, or should the existing medical path remain separate?
4. Does any generic OpenAI-compatible `base_url` support the audio transcription request and response contract?
5. What production quality and latency results justify a final default after the conformance comparison?
6. What file-size and duration ceilings should be enforced before production use?

## Product Decisions Kept Separate from Evidence

1. **Speaker policy:** Decide whether `SPEAKER_UNKNOWN` is acceptable for the first slice.
2. **Diarization placement:** Decide between the cloud diarized model and a local/RunPod post-processing stage.
3. **Medical handling:** Decide whether the existing medical path remains separate, becomes an explicit capability, or is excluded from the first cloud test.
4. **Production selection:** Treat `gpt-transcribe` and `gpt-4o-mini-transcribe` as conformance candidates only; confirm the final production default after the deferred comparison work.
5. **Realtime scope:** The first batch slice excludes realtime; any later realtime implementation remains a separate optional capability and decision.

## Contradictions, Uncertainty, and Freshness

- No direct contradiction was found among the seven admitted sources.
- The price units differ: `gpt-transcribe` is listed per minute, while `gpt-4o-mini-transcribe` and `gpt-4o-transcribe-diarize` are listed using input/output audio-token units. No cross-model cost ranking is claimed.
- `gpt-4o-transcribe-diarize` has a documented timestamp-granularity constraint. The first-slice normalizer must preserve that capability boundary.
- The model page's streaming statement and the separate Realtime reference are complementary, not permission to merge batch and realtime contracts.
- Exact language catalogs, file-size/duration ceilings, retry/cancellation behavior, privacy/retention terms, medical parity, generic compatible-audio behavior, and production quality/latency results are intentionally deferred.
- Sources were accessed on `2026-09-14` according to the recovery context. No additional snapshot or release-stability guarantee is asserted beyond the source metadata and the model-page snapshot information explicitly noted above.

## Sources

All sources below are first-party pages represented by the exact admitted Firecrawl IDs. Each source is mapped to the claims that use it.

### `firecrawl:01a0a192-b123-771c-92c8-70290e23599b`

- class: `documentation`
- title: GPT-Transcribe model page
- publisher: OpenAI Developers
- URL: https://developers.openai.com/api/docs/models/gpt-transcribe
- accessed_at: `2026-09-14`
- excerpt: "GPT-Transcribe is described as a high-accuracy speech-to-text model for completed files, streamed file transcripts, and committed Realtime turns; it supports unstructured context, keyword hints, and multiple language hints; listed price is $0.0045 per minute; audio input/text output; transcription endpoint; streaming supported; no function calling or structured outputs; rate-limit table is published."

### `firecrawl:01a0a192-b1a3-76d6-96da-e4d48f4dfddc`

- class: `documentation`
- title: GPT-4o Mini Transcribe model page
- publisher: OpenAI Developers
- URL: https://developers.openai.com/api/docs/models/gpt-4o-mini-transcribe
- accessed_at: `2026-09-14`
- excerpt: "GPT-4o Mini Transcribe is described as a high performance/fast model; listed price is $1.25 input and $5 output per 1M audio tokens; audio input/text output; transcription endpoint; rate-limit table and snapshots are published."

### `firecrawl:01a0a192-b33d-74ab-86d4-66c6b502ea00`

- class: `documentation`
- title: GPT-4o Transcribe Diarize model page
- publisher: OpenAI Developers
- URL: https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize
- accessed_at: `2026-09-14`
- excerpt: "GPT-4o Transcribe Diarize documents built-in speaker diarization, is only available in the Transcription API, lists $2.50 input and $10 output per 1M audio tokens, and publishes a rate-limit table."

### `firecrawl:01a0a189-1bbb-77ff-a824-a9421250f8fd`

- class: `documentation`
- title: Python `audio.transcriptions.create` reference
- publisher: OpenAI API Reference
- URL: https://developers.openai.com/api/reference/python/resources/audio/subresources/transcriptions/methods/create
- accessed_at: `2026-09-14`
- excerpt: "The official Python reference lists `gpt-transcribe`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, dated mini snapshots, `whisper-1`, and `gpt-4o-transcribe-diarize`; supported file formats include flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, and webm; `audio.transcriptions.create` accepts file/model/response format/language/chunking/timestamps; diarized JSON yields speaker segments and duration; word/segment timestamp granularities are unavailable for the diarized model."

### `firecrawl:01a0a189-a6f8-7325-867e-f305964fde69`

- class: `documentation`
- title: Audio transcription API reference
- publisher: OpenAI API Reference
- URL: https://developers.openai.com/api/docs/api-reference/audio/createTranscription
- accessed_at: `2026-09-14`
- excerpt: "The official transcription API reference documents JSON, diarized_json, verbose_json, and transcript-event streams, with model-specific response and timestamp constraints."

### `firecrawl:01a0a189-a83f-7103-83af-dbd507f53eb8`

- class: `documentation`
- title: Realtime API reference
- publisher: OpenAI API Reference
- URL: https://developers.openai.com/api/reference/resources/realtime
- accessed_at: `2026-09-14`
- excerpt: "The official Realtime API reference documents separate realtime session and transcription behavior."

### `firecrawl:01a0a189-1ad4-70dd-bab9-c5ae9fc3c049`

- class: `documentation`
- title: Ollama OpenAI compatibility reference
- publisher: Ollama
- URL: https://docs.ollama.com/api/openai-compatibility
- accessed_at: `2026-09-14`
- excerpt: "Ollama's official compatibility reference documents chat completions, responses, vision, tools, embeddings, and streaming, but its supported endpoint list does not document `/v1/audio/transcriptions`."

## Readiness

The exact `gentle-ai.sdd-research-capability/v1` declaration is admitted for both requested source classes, every in-scope claim maps to one or more of the seven admitted source IDs, and the narrowed research lane is complete with `status: success` and `outcome: done`. `proposal_ready` remains `false` because product decisions are still `pending`; the next action is orchestrator-owned `product-discovery`, not proposal generation.

## Key Learnings

1. The first batch conformance candidate is gpt-transcribe, while gpt-4o-mini-transcribe remains a configurable comparison candidate.
2. Official file transcription supports multiple response forms, file formats, language controls, and model-specific timestamp boundaries.
3. Diarized output supplies speaker segments and duration, but its timestamp granularity differs from verbose JSON transcription.
4. Realtime transcription remains a separate optional capability even when a model page mentions streaming support.
5. Ollama's documented OpenAI-compatible endpoint list does not establish audio transcription support.
