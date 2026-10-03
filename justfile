set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# List available recipes
default:
    @just --list

# Install dependencies and git hooks
setup:
    uv sync
    uv run pre-commit install

# Run the API with auto-reload (dashboard: http://localhost:8000, docs: /docs)
dev:
    uv run uvicorn monitor.api:create_app --factory --reload

# Run the test suite; extra arguments go to pytest
test *args:
    uv run pytest {{args}}

# Lint, check formatting and type-check
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run mypy

# Apply lint fixes and formatting
fmt:
    uv run ruff check --fix .
    uv run ruff format .

# Download the IMS dataset into data/raw/ims (~1 GB, 6 GB unpacked) and check it
data *args:
    uv run python scripts/ingest_ims.py {{args}}

# Compute features from the raw data into data/processed/features.parquet (~30 s)
features:
    uv run python scripts/extract_features.py

# Everything CI checks
check: lint test

# Build the container image and run it on port 8000
docker:
    docker build -t bearing-health-monitor .
    docker run --rm -p 8000:8000 bearing-health-monitor
