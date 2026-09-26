"""Application factory.

Startup does two things and then gets out of the way: rebuild the database from
the CSVs, and pre-compute the default run so the first page load is a read rather
than an evaluation.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.bootstrap import rebuild_database
from app.db.session import session_scope
from app.services.runs import ensure_run
from app.settings import BACKEND_DIR, settings

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

STATIC_DIR = BACKEND_DIR / "app" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.rebuild_on_startup:
        rules, report = rebuild_database()
        app.state.ingest_report = report
    else:
        from app.rules.loader import get_rules

        rules = get_rules()

    # Warm the default run. Doing it here rather than on first request keeps the
    # demo honest: the page loads at read speed, and any failure in the engine
    # surfaces at boot instead of as a slow first click.
    with session_scope() as session:
        run_id = ensure_run(session, settings.default_as_of, rules)
    logger.info("ready: default run %d for as_of=%s", run_id, settings.default_as_of)

    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Model of Care Engine",
        description=(
            "Clinical rules engine: eligibility, risk stratification, clinical "
            "needs, and role-routed tasks."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # Only needed for `npm run dev` on a separate port; the built SPA is served
    # from this same origin in the packaged app.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built frontend from the API process, if it has been built.

    One origin and one port means no CORS in the packaged app and one thing to
    run. Absent a build, the API still works on its own.
    """
    if not STATIC_DIR.exists():
        logger.info("no built frontend at %s; serving API only", STATIC_DIR)
        return

    app.mount(
        "/assets",
        StaticFiles(directory=STATIC_DIR / "assets"),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        # Client-side routing: anything not matched by /api falls back to the
        # SPA shell rather than 404ing.
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")


app = create_app()
