import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text
from .config.database import Base,engine
from .config.settings import SEED_DEMO
from . import models
from .routes import auth,libraries,samples,runs,notes,audits
from .middlewares.errors import ErrorMiddleware
from .middlewares.rate_limit import RateLimitMiddleware
from .services.run_service import queue
@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if SEED_DEMO:
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception:logging.exception("demo seed failed")
    yield
app=FastAPI(title="棱砂颜料光谱解混册",lifespan=lifespan)
app.add_middleware(ErrorMiddleware);app.add_middleware(RateLimitMiddleware)
for module in [auth,libraries,samples,runs,notes,audits]:app.include_router(module.router)
@app.get("/health")
def health():
    with engine.connect() as c:c.execute(text("select 1"))
    queue.ping();return {"status":"healthy","service":"spectral-mixture"}
