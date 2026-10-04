from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, NaiveDatetime

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


class Health(BaseModel):
    status: Literal["ok"]


class Version(BaseModel):
    commit: str


class Problem(BaseModel):
    """The body of every error the API raises itself; 422 from validation has its own shape."""

    detail: str


# Declared so the docs list the errors the handlers raise, not only FastAPI's own 422.
def not_found(description: str) -> dict[int | str, dict[str, Any]]:
    return {404: {"model": Problem, "description": description}}


UNKNOWN_EXPERIMENT = "Unknown experiment"
UNKNOWN_BEARING = "Unknown experiment or bearing"
BEFORE_START = ", or `at` before the run's first snapshot"


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
    def health() -> Health:
        """Liveness probe: the app is up and its data is loaded."""
        return Health(status="ok")

    @system.get("/version")
    def version() -> Version:
        """The commit this deployment was built from."""
        return Version(commit=settings.commit_sha)

    # Every run used new bearings, so a bearing is only unique within its experiment.
    bearing_api = APIRouter(tags=["bearings"])

    @bearing_api.get("/experiments")
    def experiments() -> list[ExperimentSummary]:
        """Every run with its machine status and each bearing's latest condition."""
        return store.experiments()

    @bearing_api.get(
        "/experiments/{experiment}", responses=not_found(UNKNOWN_EXPERIMENT + BEFORE_START)
    )
    def experiment_summary(experiment: str, at: AsOf = None) -> ExperimentSummary:
        """One run with its machine status and each bearing's condition as of `at`."""
        summary = store.experiment(experiment, at)
        if summary is None:
            raise HTTPException(404, f"Unknown experiment {experiment!r}")
        return summary

    @bearing_api.get(
        "/experiments/{experiment}/bearings",
        responses=not_found(UNKNOWN_EXPERIMENT + BEFORE_START),
    )
    def bearings(experiment: str, at: AsOf = None) -> list[BearingSummary]:
        """Each bearing of a run with its condition as of `at`."""
        return experiment_summary(experiment, at).bearings

    # Hindsight, unlike the conditions: it compares with the state documented at the end of the
    # run, so it has no `at`.
    @bearing_api.get(
        "/experiments/{experiment}/evaluation", responses=not_found(UNKNOWN_EXPERIMENT)
    )
    def experiment_evaluation(experiment: str) -> RunEvaluation:
        """How early the detector warned about each failure the dataset documents, and its false
        alarms."""
        evaluation = store.evaluation(experiment)
        if evaluation is None:
            raise HTTPException(404, f"Unknown experiment {experiment!r}")
        return evaluation

    def no_bearing(experiment: str, bearing: int) -> HTTPException:
        return HTTPException(404, f"No bearing {bearing} in experiment {experiment!r}")

    @bearing_api.get(
        "/experiments/{experiment}/bearings/{bearing}",
        responses=not_found(UNKNOWN_BEARING + BEFORE_START),
    )
    def bearing_summary(experiment: str, bearing: int, at: AsOf = None) -> BearingSummary:
        """One bearing's condition as of `at`: status, health index, the feature driving it and
        the suspected part."""
        summary = store.bearing(experiment, bearing, at)
        if summary is None:
            raise no_bearing(experiment, bearing)
        return summary

    @bearing_api.get(
        "/experiments/{experiment}/bearings/{bearing}/features",
        responses=not_found(UNKNOWN_BEARING),
    )
    def bearing_features(experiment: str, bearing: int) -> BearingFeatures:
        """The vibration features of every snapshot, per accelerometer channel."""
        features = store.features(experiment, bearing)
        if features is None:
            raise no_bearing(experiment, bearing)
        return features

    # Not "/health": that is the liveness probe. This is the series behind each condition.
    @bearing_api.get(
        "/experiments/{experiment}/bearings/{bearing}/health-index",
        responses=not_found(UNKNOWN_BEARING),
    )
    def bearing_health_index(experiment: str, bearing: int) -> HealthIndex:
        """The health index and status at every snapshot of the run."""
        health = store.health_index(experiment, bearing)
        if health is None:
            raise no_bearing(experiment, bearing)
        return health

    # Served, not recomputed in the browser: the baseline rule lives in one place.
    @bearing_api.get(
        "/experiments/{experiment}/bearings/{bearing}/ratios",
        responses=not_found(UNKNOWN_BEARING),
    )
    def bearing_ratios(experiment: str, bearing: int) -> FeatureRatios:
        """Each feature as a multiple of its median over the run's first day, at every snapshot."""
        ratios = store.ratios(experiment, bearing)
        if ratios is None:
            raise no_bearing(experiment, bearing)
        return ratios

    copilot_api = APIRouter(tags=["copilot"])

    # The run, not a bearing: the bearings share a shaft and the answer draws on all of them.
    # POST, because every call costs money and the answer can differ each time.
    @copilot_api.post(
        "/experiments/{experiment}/copilot",
        responses={
            **not_found(UNKNOWN_EXPERIMENT + BEFORE_START),
            429: {"model": Problem, "description": "Too many questions this minute"},
        },
    )
    def ask_copilot(experiment: str, question: Question, at: AsOf = None) -> CopilotAnswer:
        """Answer a question about a run as of `at`, citing the detector facts and maintenance
        knowledge it used."""
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
