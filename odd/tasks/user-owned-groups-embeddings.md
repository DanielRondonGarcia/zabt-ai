# ODD Task Ledger — user-owned-groups-embeddings

## Delivery
- Strategy: auto-chain
- Chain strategy: stacked-to-main
- Review budget: 400 changed lines
- Scope: PR1 and PR2 complete; PR3 split into PR3a and PR3b by maintainer decision.

## Tasks
- [x] PR1 — Group model, owner-scoped CRUD, Meeting.group_id assignment, migration, focused tests, migration up/down evidence.
- [x] PR2 — Configurable local-first embedding providers, Qdrant client/service, canonicalization/chunking, Celery indexing/cleanup lifecycle, triggers, retry/failure isolation, focused tests and local Qdrant evidence.
- [x] PR3a — Authorized group retrieval service/search endpoint, manual reindex endpoint, and observability.
- [x] PR3b — Retrieval leakage/integration tests, documentation, provider/Qdrant validation, and final evidence. Retrieval/integration/docs/provider-Qdrant validation are complete.
- [x] Coverage expansion — Added focused tests across the under-tested existing services; the configured backend suite now passes with 265 tests and 70% services coverage.
- [x] Verify — Native SDD verification passed with strict-TDD compliance, 10/10 spec coverage, and all validation gates verified.
- [x] Archive — Archived to `openspec/changes/archive/2026-09-18-user-owned-groups-embeddings/`; no delivery commands were run.

## Boundaries
- MCP server and in-product chat remain separate future features.
- No delivery commands (commit, push, PR, release) are authorized by this ledger.

## Evidence
- Canonical SDD tasks: `openspec/changes/user-owned-groups-embeddings/tasks.md`
- Apply progress: `openspec/changes/user-owned-groups-embeddings/apply-progress.md`
