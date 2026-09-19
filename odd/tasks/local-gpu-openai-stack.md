# ODD Task Ledger — local-gpu-openai-stack

## Delivery
- Target: local Docker Compose stack on this host.
- Runtime policy: transcription and embeddings local; transcription on the GPU worker; summary and image analysis through OpenAI only.
- Local embedding endpoint: host Ollama. Host-side probes use `127.0.0.1`; containers use Docker Desktop's `host.docker.internal` gateway because container loopback is not the host.
- No commit, push, PR, release, or destructive cleanup authorized.

## Tasks
- [x] Explore Compose, provider routing, GPU runtime, and current safe configuration values.
- [x] Set vision routing to OpenAI cloud and retain local GPU/Ollama settings without exposing secrets. Compose config validates: GPU transcription, Ollama embeddings, OpenAI summary/vision allowlist.
- [x] Start host Ollama on the local interface and ensure the configured embedding model is available. `127.0.0.1:11434/v1/embeddings` with `nomic-embed-text` returned dimension 768; Docker gateway connectivity also passed.
- [x] Rebuild the local GPU/API/worker/web/vision stack. Local and vision profile images built successfully.
- [x] Verify container health, GPU worker health, Ollama connectivity, Qdrant, and provider routing. All 10 long-running containers are up with zero restarts; GPU reports CUDA/RTX 5060; Qdrant and GPU health pass; API/web/vision/MinIO endpoints respond; no recent error logs.

## Evidence
- `docker compose --profile local --profile vision config --quiet` passed before changes.
- NVIDIA host detected: GeForce RTX 5060 Laptop GPU, driver 610.62; Docker `nvidia` runtime available.
- Current configuration has GPU transcription, Ollama embeddings, OpenAI summary credentials, but vision is still set to Ollama/deny and must be corrected.
