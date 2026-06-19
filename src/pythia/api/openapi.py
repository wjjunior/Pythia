"""Dump the OpenAPI schema to stdout: the single contract source for the dashboard's TS types.

Run via `python -m pythia.api.openapi`; the dashboard's type-gen pipes this into openapi-typescript,
so a breaking Pydantic change fails the frontend build.
"""

import json

from pythia.api.app import create_app


def main() -> None:
    print(json.dumps(create_app().openapi(), indent=2))


if __name__ == "__main__":
    main()
