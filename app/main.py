import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db, scheduler
from app.api.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(
    title="US Power POC (CAISO)",
    description=(
        "Proof of concept: live CAISO OASIS day-ahead and real-time prices for the NP15 hub. "
        "Data courtesy of the California ISO (CAISO) OASIS system — see README for attribution."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router)
