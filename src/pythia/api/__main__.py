"""Entrypoint for the FastAPI process: `python -m pythia.api`."""

import uvicorn

from pythia.api.app import app


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
