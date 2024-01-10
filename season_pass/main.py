import os

import uvicorn
from fastapi import FastAPI
from mangum import Mangum
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.status import HTTP_404_NOT_FOUND

from common import logger
from season_pass import settings, api
from season_pass.exceptions import SeasonNotFoundError, UserNotFoundError

__VERSION__ = "0.2.0"

stage = os.environ.get("STAGE", "local")

app = FastAPI(
    title="Nine Chronicles Season Pass Service",
    description="",
    version=__VERSION__,
    debug=settings.DEBUG
)


@app.middleware("http")
def log_incoming_url(request: Request, call_next):
    logger.info(f"[{request.method}] {request.url}")
    return call_next(request)


def handle_404(err: str):
    logger.error(err)
    return JSONResponse(status_code=HTTP_404_NOT_FOUND, content=err)


@app.exception_handler(SeasonNotFoundError)
def handle_season_not_found(request: Request, e: SeasonNotFoundError):
    return handle_404(str(e))


@app.exception_handler(UserNotFoundError)
def handle_user_not_found(request: Request, e: UserNotFoundError):
    return handle_404(str(e))


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
