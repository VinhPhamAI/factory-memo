FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Native dependencies used by the project's Python packages.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libgomp1 libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# uv.lock is currently ignored by Git; resolve from the committed manifest.
# Speech inference runs in speech-api; the API needs no CUDA packages.
COPY pyproject.toml README.md ./
RUN uv venv \
    && uv pip install --no-cache --torch-backend cpu -r pyproject.toml

COPY backend/ ./backend/
RUN mkdir -p /app/data/audio

EXPOSE 8005
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8005"]
