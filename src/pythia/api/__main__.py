"""Entrypoint for the FastAPI process: `python -m pythia.api`."""

import uvicorn

from pythia.api.app import app
from pythia.common.logging import configure_logging
from pythia.config import get_settings


def main() -> None:
    settings = get_settings()
    configure_logging(
        service="api",
        level=settings.log_level,
        json_logs=settings.log_json,
        logfire_token=settings.logfire_token,
        environment=settings.environment,
    )
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
