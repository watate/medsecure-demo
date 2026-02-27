FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.6.6 /uv /uvx /bin/

WORKDIR /app

ENV PYTHONUNBUFFERED=1

# Install dependencies first (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Copy application code
COPY app/ app/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
