# Multi-stage on purpose: a React/Vite frontend can be added later as an extra
# "frontend" stage whose dist/ output is copied into the runtime image.

FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN useradd --create-home --uid 1000 app
COPY --from=build /install /usr/local
COPY frontend ./frontend
COPY data/processed ./data/processed
ENV MONITOR_FRONTEND_DIR=/app/frontend
USER app
EXPOSE 8000
CMD ["uvicorn", "monitor.api:app", "--host", "0.0.0.0", "--port", "8000"]
