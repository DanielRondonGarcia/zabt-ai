# ODD Feature: Integrations layout and generic S3 endpoint
Branch: `feature/integrations-layout-s3-endpoint`
Status: in progress

## Specs
1. **S1** — “mmm si toca mejorar aca, primero re organiza bien la pantalla de integraciones, eso ya parece un spaguetti”
2. **S2** — “segundo, haz la integración s3 generica, que se pueda poner el endpoint completo con dominio, por ejemplo usar uno de oracle storage”

## Tasks
- T1 — S1 — Reorganize Integrations into role-aware My integrations and Instance settings tabs; keep non-superuser account integrations and current access rules intact — inline — done; frontend build passed; targeted lint timed out (not retried), and no frontend test runner is configured.
- T2 — S2 — Add a validated optional full S3-compatible endpoint URL to admin-managed storage, use it for internal and browser-presigned clients, retain AWS-default behavior when blank, and update UI/docs — inline — pending.
- T3 — S2 — Independently verify the custom endpoint boundary, storage client routing, credential handling, migration, and compatibility with endpoint-omitted AWS settings — gentle-ai-verify — pending.

## Log
- L1 — User request (verbatim): “mmm si toca mejorar aca, primero re organiza bien la pantalla de integraciones, eso ya parece un spaguetti y segundo, haz la integración s3 generica, que se pueda poner el endpoint completo con dominio, por ejemplo usar uno de oracle storage”
- L2 — Read-only exploration: Integrations currently stacks instance-wide authentication mode, global storage, Microsoft Entra settings, and personal Graph/calendar connections on one long page. Storage is persisted via the superuser-only `/storage/configuration` API; internal provider identifier `aws_s3` is also stored on meetings. `S3Storage` uses boto3 SigV4 for internal and presigned clients but admin-managed runtime config currently passes no custom endpoint.
- L3 — Scoped research: Boto3 supports `endpoint_url`; Oracle OCI documents its S3 Compatibility API host format and supports SigV4 plus path-style addressing. References: https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm and https://docs.oracle.com/en-us/iaas/Content/Object/s3-virtual-style.htm
- L4 — Design decision: Use role-aware tabs to separate personal integrations from instance-wide settings; default system superusers to Instance settings while non-superusers stay on My integrations. Keep current account and admin actions in their respective views. Preserve the stored `aws_s3` provider contract, add an optional HTTPS endpoint field for S3-compatible services, use the configured endpoint for server and browser signing with path-style addressing, and keep blank endpoint behavior equivalent to AWS's current default.
- L5 — Security boundary: Only system superusers may save the endpoint and credentials. Validate the endpoint as an HTTPS base URL without embedded credentials, query, or fragment; do not expose the S3 secret. No endpoint probing, AWS credentials, release, or deployment is in scope.
- L6 — Work-unit boundary: T1 changes only the Integrations page presentation/organization and preserves existing actions. T2 adds endpoint persistence/API/runtime routing, the Storage view input, additive migration, focused tests, and configuration documentation. Keep tests/docs with their behavior commit; run the independent verifier because the configurable endpoint changes an outbound credential-bearing connection boundary (high-risk item 2).
- L7 — Engram task-mirror save was attempted immediately after planning but failed with `unable to open database file (14)`. Local task document and visible todo remain current; do not retry automatically.
- L8 — T1 refined after reviewing the screenshot and page structure: the primary split is personal integrations versus instance-wide settings. Keep sign-in, storage, and Entra controls together in the instance-settings view rather than adding nested tabs; this keeps the navigation shallow and preserves the existing settings cards.
- L9 — T1 outcome: added role-aware My integrations / Instance settings tabs; superusers default to instance settings, non-superusers retain account integrations only, and Graph deployment readiness now lives with instance settings. `npm run build` passed (job-8, exit 0); `git diff --check` passed. Targeted ESLint timed out at 30 seconds and was not retried; the frontend package has no test script, and no browser interaction test was available. Risk: none; only the route's presentation and grouping changed, with handlers and authorization gates preserved.
