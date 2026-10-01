# Bearing Health Monitor

Condition monitoring for rolling bearings, built on the NASA IMS run-to-failure dataset:
vibration features → anomaly detection → health index → REST API → dashboard.

## Run locally

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash; on Linux/macOS: .venv/bin/activate
pip install -e ".[dev]"
uvicorn monitor.api:app --reload
```

- Dashboard: http://localhost:8000
- API docs: http://localhost:8000/docs

## Tests

```bash
pytest
ruff check . && ruff format --check .
```

## Architecture

- `src/monitor/`: Python package (API under `/api/v1`)
- `frontend/`: static dashboard, served by the API at `/`. The directory is configurable
  via `MONITOR_FRONTEND_DIR`, so a React/Vite build (`frontend/dist`) can replace it without
  backend changes. For a separate dev server, set `MONITOR_CORS_ORIGINS='["http://localhost:5173"]'`.
- `data/processed/`: precomputed features shipped with the image. Raw data is never committed.

## Data

NASA IMS Bearing Dataset (J. Lee, H. Qiu, G. Yu, J. Lin, and Rexnord Technical Services,
"Bearing Data Set", NASA Prognostics Data Repository).
