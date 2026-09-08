"""Export the OpenAPI spec to docs/openapi.json.

Generates the spec from the FastAPI app and removes endpoints that are not
part of the core Agent Protocol surface (custom routes, debug probes).

NOTE: Environment variables must be set before importing the app, so the
imports below are intentionally placed after os.environ calls.
"""

import json
import logging
import os
from pathlib import Path

logging.disable(logging.CRITICAL)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://export:export@localhost:5432/export")

import structlog  # noqa: E402

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

from agent_server.app.main import OPENAPI_TAGS, create_app  # noqa: E402
from agent_server.config.settings import settings  # noqa: E402


def main() -> None:
    """Export the OpenAPI spec to docs/openapi.json."""
    schema = create_app().openapi()

    schema["info"]["title"] = settings.app.PROJECT_NAME
    schema["info"]["version"] = settings.app.VERSION
    schema["info"]["description"] = "Production-ready Agent Protocol server"

    core_tags: set[str] = {t["name"] for t in OPENAPI_TAGS}
    paths_to_remove: list[str] = []
    for path, methods in schema.get("paths", {}).items():
        for _method, info in methods.items():
            tags = set(info.get("tags", []))
            if not tags or not tags & core_tags:
                paths_to_remove.append(path)
                break
    for path in paths_to_remove:
        del schema["paths"][path]

    out = Path("docs/openapi.json")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Exported {len(schema['paths'])} paths to {out}")


if __name__ == "__main__":
    main()
