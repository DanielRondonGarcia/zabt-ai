# multimodal-summary Specification

## Purpose

This capability generates summary-only multimodal context from existing timestamped spoken and visual segments while preserving transcript reliability. Intelligence extraction remains transcript-only. It MUST NOT add a fused-context schema migration, new viewer, broad UI redesign, mandatory external VLM, or automatic provider switching.

## Requirements

### Requirement: Correlate bounded evidence with provenance

The system MUST build chronological windows using half-open intervals `[start,end)`. A spoken and visual segment overlap only when `spoken.start < visual.end` AND `visual.start < spoken.end`, whether either interval encloses the other; touching boundaries MUST NOT overlap. Every included item MUST retain its source ID, timestamps, source fields, relevance, and provenance.

#### Scenario: Enclosing and boundary intervals

- GIVEN a visual interval encloses a spoken interval, a spoken interval encloses another visual interval, and a third starts exactly when the spoken interval ends
- WHEN the context builder correlates both intervals
- THEN it includes both enclosing overlaps and excludes the boundary-touching item

#### Scenario: Evidence can be traced

- GIVEN a window contains spoken and visual evidence
- WHEN a summary reference is emitted
- THEN the reference includes the contributing source IDs and bounded timestamps

### Requirement: Deduplicate conservative visual context

The system MUST prioritize meaningful changes in screens, slides, dashboards, documents, logs, code, or errors and SHOULD de-emphasize repeated camera frames. Near-identical visual evidence MUST appear once across adjacent windows unless a meaningful change justifies repetition.

#### Scenario: Repeated frame suppression

- GIVEN adjacent windows contain the same visual evidence without a meaningful change
- WHEN multimodal context is built
- THEN the visual evidence is emitted once while chronological spoken content remains available

### Requirement: Synthesize complete long-meeting context within explicit bounds

The system MUST use configurable temporal chunks, bounded partial summaries, and a final hierarchical synthesis. It MUST measure the request budget and MUST NOT silently truncate spoken or visual evidence; material that cannot fit MUST be moved to another bounded unit or produce an explicit bounded warning.

#### Scenario: Long meeting exceeds one request

- GIVEN a meeting exceeds the configured context budget
- WHEN a summary is generated
- THEN all source intervals are assigned to bounded chunks and partial results are synthesized without silent loss

### Requirement: Separate evidence, inference, and uncertainty

Multimodal prompts and outputs MUST label `SPOKEN CONTENT`, `VISUAL CONTEXT`, and `INFERENCE/UNCERTAINTY`. Observed visual facts MUST remain distinct from inference, and decisions, commitments, owners, and timestamps MUST be stated only when supported by evidence; ambiguous claims MUST be marked uncertain or omitted.

#### Scenario: Ambiguous visual does not invent a commitment

- GIVEN a visual frame suggests an action but no spoken or visual evidence identifies an owner or due time
- WHEN the summary is synthesized
- THEN it reports the observed evidence and uncertainty without inventing a commitment, owner, or timestamp

### Requirement: Preserve summary contracts and verification coverage

The system MUST preserve existing summary formats, custom-template behavior, APIs, editing/restoration and export flows, viewer timestamp seeking, and clients that do not know visual fields. Automated tests and documentation MUST cover overlap boundaries, deduplication, budgets, fallback, labels, provenance, and compatibility. Intelligence extraction MUST continue to use transcript-only input.

#### Scenario: Existing consumer remains compatible

- GIVEN an existing API or UI consumer requests a summary without visual support
- WHEN a multimodal-capable meeting is processed
- THEN the consumer receives the existing contract and can use the existing viewer and timestamp seeking
