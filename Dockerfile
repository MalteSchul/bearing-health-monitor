# Multi-stage on purpose: a React/Vite frontend can be added later as an extra
# "frontend" stage whose dist/ output is copied into the runtime image.

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.21 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN useradd --create-home --uid 1000 app
COPY --from=build /app/.venv /app/.venv
COPY frontend ./frontend
COPY data/processed ./data/processed
ENV PATH="/app/.venv/bin:$PATH" \
    MONITOR_FRONTEND_DIR=/app/frontend
# Last, because it changes on every build and invalidates every layer after it.
ARG COMMIT_SHA=dev
ENV MONITOR_COMMIT_SHA=$COMMIT_SHA
USER app
EXPOSE 8000
CMD ["uvicorn", "monitor.api:app", "--host", "0.0.0.0", "--port", "8000"]
