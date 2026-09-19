"""Composition of the domain REST surfaces (langgraph.json http.app entry).

One entry point, many domains: add your domain's router here — the server
merges this app with its core routers and auth (route_merger). Domain
surfaces stay in their packages (src/<domain>/api.py); this file only wires.

The lifespan chains domain lifecycles (hub business-schema migrations, ML
model load/unload) with the server's own — route_merger combines them, so
the framework schema always migrates before this runs.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from hub.api import public_router as hub_public_router
from hub.api import router as hub_router
from hub.migrate import run_hub_migrations_if_needed
from ml import service as ml_service
from ml.api import router as ml_router
from shop.api import router as shop_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Alembic env.py owns its own event loop — hand off to a thread.
    await asyncio.to_thread(run_hub_migrations_if_needed)
    ml_service.load_model()
    yield
    ml_service.unload_model()


app = FastAPI(title="Domain APIs", lifespan=lifespan)

app.include_router(shop_router)
app.include_router(ml_router)
app.include_router(hub_router)
app.include_router(hub_public_router)
