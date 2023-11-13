import os

import uvicorn
from fastapi import FastAPI

__VERSION__ = "0.0.1"

from mangum import Mangum
from starlette.requests import Request
from starlette.responses import FileResponse

from common import logger
from season_pass import settings, api

stage = os.environ.get("STAGE", "local")

app = FastAPI(
    title="Nine Chronicles Season Pass Service",
    description="",
    version=__VERSION__,
    root_path=f"/{stage}" if stage != "local" else "",
    debug=settings.DEBUG
)


@app.middleware("http")
def log_incoming_url(request: Request, call_next):
    logger.info(f"[{request.method}] {request.url}")
    return call_next(request)


@app.get("/ping", tags=["Default"])
def ping():
    return "pong"


@app.get("/robots.txt", response_class=FileResponse, tags=["Default"], summary="Return robots.txt")
def robots():
    return "season_pass/robots.txt"


app.include_router(api.router)
# app.mount("/_app", StaticFiles(directory="iap/frontend/build/_app"), name="static")

handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run("main:app", reload=settings.DEBUG)
