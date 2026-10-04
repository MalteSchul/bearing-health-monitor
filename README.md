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
- `GET /api/v1/experiments/{experiment}/bearings/{bearing}`: one bearing and its condition; every
  shorter prefix (`.../bearings`, `/experiments/{experiment}`, `/experiments`) is a resource too.
  Add `?at=2004-02-16T04:00` to get the condition as it was known at that time. An experiment
  also reports its `machine`: the worst bearing's status and which bearings have it
- `GET /api/v1/experiments/{experiment}/bearings/{bearing}/features`: feature trends of one bearing
- `GET /api/v1/experiments/{experiment}/bearings/{bearing}/health-index`: health index and status
  per snapshot: `baseline`, `ok`, `crosstalk`, `alert` (plan the replacement), `danger` (act now)
- `GET /api/v1/experiments/{experiment}/bearings/{bearing}/ratios`: each feature relative to its
  baseline, what the detector compares (the health index is the largest)
- `GET /api/v1/experiments/{experiment}/evaluation`: hindsight per bearing (detected, missed,
  false alarm, quiet) and the operating hours each level came before the run ended
- `POST /api/v1/experiments/{experiment}/copilot?at=…` with `{"question": "...", "bearing": 3}`:
  the copilot's answer, built only from cited facts (the detector at that moment and
  `src/monitor/knowledge.toml`). Needs `ANTHROPIC_API_KEY` in the environment or in `.env`
  (copy `.env.example`); without it, only the facts
- `frontend/`: static dashboard, served by the API at `/`. `?run=set2&bearing=1&at=2004-02-16T04:12:39`
  opens a replay at that moment; without `bearing` it shows the whole rig. The directory is configurable
  via `MONITOR_FRONTEND_DIR`, so a React/Vite build (`frontend/dist`) can replace it without
  backend changes. For a separate dev server, set `MONITOR_CORS_ORIGINS='["http://localhost:5173"]'`.
- `data/processed/`: precomputed features shipped with the image. Raw data is never committed.

## Data

[NASA IMS Bearing Dataset](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/)
(J. Lee, H. Qiu, G. Yu, J. Lin, and Rexnord Technical Services, "Bearing Data Set",
NASA Prognostics Data Repository).

```bash
just data      # download (~1 GB) into data/raw/ims and validate it
just features  # compute data/processed/features.parquet (committed; rerun after changing features)
just evaluate  # detector results per bearing and vs. rms alone (tuned on set 2, tested on sets 1 and 3)
```

Unpacking needs bsdtar (built into Windows and macOS; `apt install libarchive-tools` on Linux).
