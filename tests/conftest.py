"""Root test config: derandomize Hypothesis in CI so a flaky property failure reproduces (PYT-108).

Locally the default (randomized) profile keeps exploring new inputs; under CI (the `CI` env var, set
by GitHub Actions) a fixed seed makes any failure deterministically reproducible from the report.
"""

import os

from hypothesis import settings

settings.register_profile("ci", derandomize=True)
if os.environ.get("CI"):
    settings.load_profile("ci")
