from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import NaiveDatetime

from monitor.config import Settings
from monitor.copilot import (
    ClaudeWriter,
    Copilot,
    CopilotAnswer,
    Question,
    RateLimit,
    RateLimited,
    UnknownBearing,
    Writer,
)
from monitor.evaluation import RunEvaluation
from monitor.knowledge import Knowledge
from monitor.store import (
    BearingFeatures,
    BearingSummary,
    ExperimentSummary,
    FeatureRatios,
    FeatureStore,
    HealthIndex,
    NoDataYet,
)

API_PREFIX = "/api"
# The path names the resource, the query picks the moment. The dataset's timestamps have no
# offset, so one sent with "Z" or "+02:00" would be ambiguous and is rejected.
AsOf = Annotated[
    NaiveDatetime | None,
    Query(
        description="Condition as of this local rig time, without offset. Latest if omitted.",
        examples=["2004-02-16T04:00:00"],
    ),
]


def create_app(settings: Settings | None = None, writer: Writer | None = None) -> FastAPI:
    """`writer` replaces the model call, for tests; by default the configured model, if a key is
    set."""
    settings = settings or Settings()
    # Loaded before serving: a missing file stops the container from starting, so a broken
    # revision never takes traffic.
    store = FeatureStore.load(settings.features_path)
    key = settings.anthropic_api_key
    if writer is None and key is not None:
        writer = ClaudeWriter(key.get_secret_value(), settings.copilot_model)
    copilot = Copilot(
        store, Knowledge.load(), writer, RateLimit(settings.copilot_questions_per_minute)
    )
    # The commit is the version: every merge to main is deployed, nobody cuts releases.
    app = FastAPI(title="Bearing Health Monitor", version=settings.commit_sha)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
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
    bearing_api = APIRouter(tags=["bearings"])

    @bearing_api.get("/experiments")
    def experiments() -> list[ExperimentSummary]:
        return store.experiments()

    @bearing_api.get("/experiments/{experiment}")
    def experiment_summary(experiment: str, at: AsOf = None) -> ExperimentSummary:
        summary = store.experiment(experiment, at)
        if summary is None:
            raise HTTPException(404, f"Unknown experiment {experiment!r}")
        return summary

    @bearing_api.get("/experiments/{experiment}/bearings")
    def bearings(experiment: str, at: AsOf = None) -> list[BearingSummary]:
        return experiment_summary(experiment, at).bearings

    # Hindsight, unlike the conditions: it compares with the state documented at the end of the
    # run, so it has no `at`.
    @bearing_api.get("/experiments/{experiment}/evaluation")
    def experiment_evaluation(experiment: str) -> RunEvaluation:
        evaluation = store.evaluation(experiment)
        if evaluation is None:
            raise HTTPException(404, f"Unknown experiment {experiment!r}")
        return evaluation

    def no_bearing(experiment: str, bearing: int) -> HTTPException:
        return HTTPException(404, f"No bearing {bearing} in experiment {experiment!r}")

    @bearing_api.get("/experiments/{experiment}/bearings/{bearing}")
    def bearing_summary(experiment: str, bearing: int, at: AsOf = None) -> BearingSummary:
        summary = store.bearing(experiment, bearing, at)
        if summary is None:
            raise no_bearing(experiment, bearing)
        return summary

    @bearing_api.get("/experiments/{experiment}/bearings/{bearing}/features")
    def bearing_features(experiment: str, bearing: int) -> BearingFeatures:
        features = store.features(experiment, bearing)
        if features is None:
            raise no_bearing(experiment, bearing)
        return features

    # Not "/health": that is the liveness probe. This is the series behind each condition.
    @bearing_api.get("/experiments/{experiment}/bearings/{bearing}/health-index")
    def bearing_health_index(experiment: str, bearing: int) -> HealthIndex:
        health = store.health_index(experiment, bearing)
        if health is None:
            raise no_bearing(experiment, bearing)
        return health

    # Served, not recomputed in the browser: the baseline rule lives in one place.
    @bearing_api.get("/experiments/{experiment}/bearings/{bearing}/ratios")
    def bearing_ratios(experiment: str, bearing: int) -> FeatureRatios:
        ratios = store.ratios(experiment, bearing)
        if ratios is None:
            raise no_bearing(experiment, bearing)
        return ratios

    copilot_api = APIRouter(tags=["copilot"])

    # The run, not a bearing: the bearings share a shaft and the answer draws on all of them.
    # POST, because every call costs money and the answer can differ each time.
    @copilot_api.post("/experiments/{experiment}/copilot")
    def ask_copilot(experiment: str, question: Question, at: AsOf = None) -> CopilotAnswer:
        try:
            answer = copilot.ask(experiment, question, at)
        except UnknownBearing as exc:
            raise HTTPException(422, str(exc)) from exc
        except RateLimited as exc:
            raise HTTPException(
                429, "Too many questions, try again in a minute", headers={"Retry-After": "60"}
            ) from exc
        if answer is None:
            raise HTTPException(404, f"Unknown experiment {experiment!r}")
        return answer

    # The bearing exists, but nothing was known about it yet at that time.
    @app.exception_handler(NoDataYet)
    def no_data_yet(request: Request, exc: NoDataYet) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    # The version is the prefix, the tags only group the docs: one router per version holds the
    # topic routers. Routes are copied on inclusion, so this comes after they are all declared.
    v1 = APIRouter(prefix=f"{API_PREFIX}/v1")
    v1.include_router(bearing_api)
    v1.include_router(copilot_api)

    app.include_router(system)
    app.include_router(v1)

    # Mounted last so it never shadows API routes.
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app
