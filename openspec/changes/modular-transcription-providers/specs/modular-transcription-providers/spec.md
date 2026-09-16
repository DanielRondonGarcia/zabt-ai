# Modular Transcription Providers Specification

## Purpose

Zabt SHALL expose validated, provider-neutral batch transcription while preserving transcript semantics and local/RunPod medical processing.

## Requirements

### Requirement: Validated provider registry and selection

The registry MUST expose `gpu-local`, `runpod`, and `openai-file`. `.env` `TRANSCRIPTION_*` settings MUST own provider, model, optional credential override, language, timestamps, and proven options; `OPENAI_API_KEY` is the documented fallback credential for `openai-file` when the override is empty, while `OPENAI_BASE_URL`, `OPENAI_MODEL`, and vision settings MUST NOT configure transcription. Invalid settings MUST fail explicitly without fallback.

#### Scenario: Select the configured provider

- GIVEN `.env` selects `openai-file` with `gpt-transcribe` and either a valid transcription override or a valid shared `OPENAI_API_KEY`
- WHEN a batch job is selected
- THEN the configured adapter and model are recorded and used

#### Scenario: Reject invalid selection

- GIVEN an unknown provider, both OpenAI credentials are missing, or an incompatible option
- WHEN settings are loaded
- THEN validation fails and no alternate provider is selected

### Requirement: Provider-neutral batch contracts

Batch request, result, and capability contracts MUST cover media input, text, language, optional segments, words, speakers, timestamps, duration, provider/model metadata, usage units, progress, and errors. Missing values MUST remain absent or unknown. Realtime MAY have a separate contract but MUST NOT enter batch.

#### Scenario: Normalize a partial result

- GIVEN a provider returns text but no words, speakers, or duration
- WHEN the result is normalized
- THEN returned values persist and unavailable fields remain absent or unknown

#### Scenario: Keep realtime separate

- GIVEN a caller requests realtime through the batch interface
- WHEN capabilities are checked
- THEN an explicit unsupported-capability error is returned

### Requirement: Preserve local, RunPod, and medical behavior

Existing `gpu-local` and `runpod` entry points and wire payloads MUST remain compatible. Medical transcription MUST remain on local/RunPod MedASR; OpenAI general transcription MUST NOT claim or replace medical processing.

#### Scenario: Preserve an existing medical job

- GIVEN a medical job targets local or RunPod execution
- WHEN the worker submits it
- THEN the existing MedASR path and persistence behavior remain intact

### Requirement: Official OpenAI file-transcription adapter

`openai-file` MUST call `audio.transcriptions.create` with a file object and configured model. `gpt-transcribe` is the first candidate; `gpt-4o-mini-transcribe` MUST be selectable for comparison. The adapter MUST validate documented formats, forward language and supported response/timestamp controls, and preserve returned duration and usage in original units.

#### Scenario: Submit a supported file

- GIVEN a readable WAV or MP3 and a configured candidate model
- WHEN batch transcription runs
- THEN the file object, model, language, and valid controls are submitted

#### Scenario: Reject unsupported input

- GIVEN an unreadable file or unsupported model option
- WHEN the adapter validates the request
- THEN it returns a normalized input/capability error without conversion or fallback

### Requirement: Capability-aware normalization and failure handling

Capabilities MUST gate unsupported formats, response modes, timestamps, words, speakers, and duration. Normalization MUST NOT invent fields; absent speakers MAY be `SPEAKER_UNKNOWN`. Progress and errors MUST be normalized, and provider failures MUST NOT silently fall back. Speaker-required mode MUST use a cloud-diarizing provider; `gpt-4o-transcribe-diarize` is optional/future.

#### Scenario: Normalize missing speakers

- GIVEN the selected provider returns no speaker labels
- WHEN the first-slice result is normalized
- THEN speakers are absent or `SPEAKER_UNKNOWN`, never invented

#### Scenario: Surface provider failure

- GIVEN a provider rejects the file or response format
- WHEN the worker receives the failure
- THEN normalized error/progress state is reported and no alternate provider is attempted

### Requirement: CLI, worker, persistence, and fixture conformance

CLI and worker MUST use the same registry and contracts and remain readable by existing persistence without schema migration. The conformance fixture MUST run `gpt-transcribe` and configurable `gpt-4o-mini-transcribe`, recording normalized text, language, supported timing, speaker policy, duration, metadata, capability gaps, and original usage/cost units; WAV and MP3 MUST pass or be explicitly marked.

#### Scenario: Accept the shared fixture

- GIVEN the declared audio fixture and transcript oracle
- WHEN both configured candidates execute
- THEN the acceptance record contains normalized fields, gaps, metadata, and original usage units

#### Scenario: Preserve CLI and worker compatibility

- GIVEN a result has unavailable optional fields
- WHEN CLI or worker persistence runs
- THEN existing consumers remain readable without fabricated values or schema changes

### Requirement: Enforce first-slice boundaries

The first slice MUST NOT expose realtime, OpenAI medical parity, generic compatible-audio `base_url`, or unsupported Ollama audio. Future support MUST require a separately verified contract and capability declaration.

#### Scenario: Reject out-of-scope audio integrations

- GIVEN a caller selects generic compatible audio or unsupported Ollama audio
- WHEN the registry validates the selection
- THEN it rejects it explicitly and does not register or fall back
