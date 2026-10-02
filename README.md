# Bearing Health Monitor

Condition monitoring for rolling bearings, built on the NASA IMS run-to-failure dataset:
vibration features → anomaly detection → health index → REST API → dashboard.

## Run locally

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/)
(Windows: `winget install astral-sh.uv Casey.Just`).

```bash
just setup   # install dependencies and git hooks
just dev     # run the API with auto-reload
```

- Dashboard: http://localhost:8000
- API docs: http://localhost:8000/docs

## Checks

```bash
just lint    # ruff lint + format check, mypy
just test    # pytest
just check   # both, same as CI
just fmt     # apply lint fixes and formatting
```

Run `just` to list all recipes.

## Architecture

- `src/monitor/`: Python package (`/api/health` and `/api/version` for operations, business endpoints under `/api/v1`)
- `frontend/`: static dashboard, served by the API at `/`. The directory is configurable
  via `MONITOR_FRONTEND_DIR`, so a React/Vite build (`frontend/dist`) can replace it without
  backend changes. For a separate dev server, set `MONITOR_CORS_ORIGINS='["http://localhost:5173"]'`.
- `data/processed/`: precomputed features shipped with the image. Raw data is never committed.

## Data

NASA IMS Bearing Dataset (J. Lee, H. Qiu, G. Yu, J. Lin, and Rexnord Technical Services,
"Bearing Data Set", NASA Prognostics Data Repository).
