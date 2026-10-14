FROM ghcr.io/astral-sh/uv:0.12.23 AS uv
FROM python:3.14-slim

COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && groupadd --gid 10001 cache \
    && useradd --uid 10001 --gid cache --no-create-home cache \
    && mkdir /data && chown cache:cache /data

ENV PATH="/app/.venv/bin:$PATH" CACHE_DATA_DIR=/data PYTHONUNBUFFERED=1
USER cache
EXPOSE 8000
CMD ["uvicorn", "caching_service.api:app", "--host", "0.0.0.0", "--port", "8000"]
