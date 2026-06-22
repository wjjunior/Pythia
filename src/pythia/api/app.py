"""FastAPI application (§2.1, §3.5): REST snapshots + SSE, OpenAPI is the contract source.

Route handlers stay thin (validate → delegate → serialize); business logic lives in the use cases,
services and the pure core. TypeScript types are generated from this app's OpenAPI schema.
"""

from fastapi import FastAPI

from pythia.api.events import EventBroadcaster
from pythia.api.routers import (
    calibration,
    cost,
    estimates,
    events,
    health,
    markets,
    paper,
    portfolio,
    radar,
    resolutions,
    review,
    run,
    simulation,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Pythia", version="0.1.0")
    app.state.broadcaster = EventBroadcaster()
    routers = (
        markets,
        radar,
        estimates,
        paper,
        portfolio,
        calibration,
        simulation,
        cost,
        review,
        resolutions,
        run,
        events,
        health,
    )
    for module in routers:
        app.include_router(module.router)
    return app


app = create_app()
