# GPU Worker: CUDA runtime + ML deps via uv + source code
# Models download at first startup (not baked in)
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# System dependencies + Python + FFmpeg 6 (Ubuntu 24.04 default).
# Keep the image independent of third-party PPAs so Docker builds work in
# environments where Launchpad is unavailable or restricted.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    ffmpeg \
    libavcodec-dev \
    libavformat-dev \
    libavutil-dev \
    libswresample-dev \
    libswscale-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Ensure libraries are indexed
RUN ldconfig

# Install uv (pinned version for reproducible builds)
COPY --from=ghcr.io/astral-sh/uv:0.7.8 /uv /uvx /bin/

WORKDIR /app

# Copy dependency files (cached layer — rebuilds only when deps change)
COPY pyproject.toml uv.lock ./

# Install dependencies with the explicit GPU extra and cache mount for faster retries
ENV UV_HTTP_TIMEOUT=1200
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project --extra gpu

# Activate venv via PATH
ENV PATH="/app/.venv/bin:$PATH"

# WhisperX alignment uses NLTK's language-aware Punkt tables. Keep the data in
# the image so Spanish and other supported languages work on a clean host.
ENV NLTK_DATA="/usr/local/share/nltk_data"
RUN mkdir -p "${NLTK_DATA}/tokenizers" \
    && curl --fail --silent --show-error --location --retry 5 --retry-delay 2 --retry-all-errors \
        "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt.zip?download=1" \
        --output /tmp/punkt.zip \
    && curl --fail --silent --show-error --location --retry 5 --retry-delay 2 --retry-all-errors \
        "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip?download=1" \
        --output /tmp/punkt_tab.zip \
    && python -c "import zipfile; zipfile.ZipFile('/tmp/punkt.zip').extractall('${NLTK_DATA}/tokenizers'); zipfile.ZipFile('/tmp/punkt_tab.zip').extractall('${NLTK_DATA}/tokenizers')" \
    && python -c "import nltk; nltk.data.find('tokenizers/punkt_tab/spanish')" \
    && rm -f /tmp/punkt.zip /tmp/punkt_tab.zip

# Copy source code (fast layer — rebuilds on every code change)
COPY scripts/ scripts/
COPY src/ src/

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import httpx; httpx.get('http://localhost:8001/health')" || exit 1

CMD ["python", "-m", "src.main"]
