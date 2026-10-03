from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import NaiveDatetime

from monitor.config import Settings
from monitor.store import (
    BearingFeatures,
    BearingSummary,
    ExperimentSummary,
    FeatureStore,
    HealthIndex,
    NoDataYet,
)

API_PREFIX = "/api"
# The path names the resource, the query picks the moment. The dataset's timestamps have no
# offset, so one sent with "Z" or "+02:00" would be ambiguous and is rejected.
AsOf = Annotated[
    NaiveDatetime | None,
    Query(description="Condition as of this time, e.g. 2004-02-16T04:00. Latest if omitted."),
]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    # Loaded before serving: a missing file stops the container from starting, so a broken
    # revision never takes traffic.
    store = FeatureStore.load(settings.features_path)
    # The commit is the version: every merge to main is deployed, nobody cuts releases.
    app = FastAPI(title="Bearing Health Monitor", version=settings.commit_sha)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET"],
            allow_headers=["*"],
        )
    # Feature series are long arrays of similar numbers and shrink to about a third.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Unversioned: these describe the deployment, not the API contract.
    # Business endpoints go under /api/v1 so they can move to /api/v2 on their own.
    system = APIRouter(prefix=API_PREFIX, tags=["system"])

    @system.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @system.get("/version")
    def version() -> dict[str, str]:
        return {"commit": settings.commit_sha}

    # Every run used new bearings, so a bearing is only unique within its experiment.
    v1 = APIRouter(prefix=f"{API_PREFIX}/v1", tags=["bearings"])

    @v1.get("/experiments")
    def experiments() -> list[ExperimentSummary]:
        return store.experiments()

    @v1.get("/experiments/{experiment}")
    def experiment_summary(experiment: str, at: AsOf = None) -> ExperimentSummary:
        summary = store.experiment(experiment, at)
        if summary is None:
            raise HTTPException(404, f"Unknown experiment {experiment!r}")
        return summary

    @v1.get("/experiments/{experiment}/bearings")
    def bearings(experiment: str, at: AsOf = None) -> list[BearingSummary]:
        return experiment_summary(experiment, at).bearings

    def no_bearing(experiment: str, bearing: int) -> HTTPException:
        return HTTPException(404, f"No bearing {bearing} in experiment {experiment!r}")

    @v1.get("/experiments/{experiment}/bearings/{bearing}")
    def bearing_summary(experiment: str, bearing: int, at: AsOf = None) -> BearingSummary:
        summary = store.bearing(experiment, bearing, at)
        if summary is None:
            raise no_bearing(experiment, bearing)
        return summary

    @v1.get("/experiments/{experiment}/bearings/{bearing}/features")
    def bearing_features(experiment: str, bearing: int) -> BearingFeatures:
        features = store.features(experiment, bearing)
        if features is None:
            raise no_bearing(experiment, bearing)
        return features

    # Not "/health": that is the liveness probe. This is the series behind each condition.
    @v1.get("/experiments/{experiment}/bearings/{bearing}/health-index")
    def bearing_health_index(experiment: str, bearing: int) -> HealthIndex:
        health = store.health_index(experiment, bearing)
        if health is None:
            raise no_bearing(experiment, bearing)
        return health

    # The bearing exists, but nothing was known about it yet at that time.
    @app.exception_handler(NoDataYet)
    def no_data_yet(request: Request, exc: NoDataYet) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.include_router(system)
    app.include_router(v1)

    # Mounted last so it never shadows API routes.
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app
