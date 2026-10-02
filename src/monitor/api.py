from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from monitor.config import Settings

API_PREFIX = "/api"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    # The commit is the version: every merge to main is deployed, nobody cuts releases.
    app = FastAPI(title="Bearing Health Monitor", version=settings.commit_sha)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    # Unversioned: these describe the deployment, not the API contract.
    # Business endpoints go under /api/v1 so they can move to /api/v2 on their own.
    system = APIRouter(prefix=API_PREFIX, tags=["system"])

    @system.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @system.get("/version")
    def version() -> dict[str, str]:
        return {"commit": settings.commit_sha}

    app.include_router(system)

    # Mounted last so it never shadows API routes.
    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")

    return app


app = create_app()
