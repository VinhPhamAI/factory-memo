FROM ghcr.io/astral-sh/uv:0.7.13 AS uv
FROM python:3.12.11-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable && \
    groupadd --gid 10001 agent && useradd --uid 10001 --gid agent --no-create-home agent
USER agent
EXPOSE 8010
CMD ["denso-agent", "serve", "--host", "0.0.0.0"]
