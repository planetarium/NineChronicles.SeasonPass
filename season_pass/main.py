import os

import uvicorn
from fastapi import FastAPI
from mangum import Mangum
from requests import ReadTimeout
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.status import (
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from common import logger
from season_pass import api, settings
from season_pass.exceptions import (
    InvalidSeasonError,
    NotPremiumError,
    SeasonNotFoundError,
    ServerOverloadError,
    UserNotFoundError,
)

__VERSION__ = "0.3.1"

stage = os.environ.get("STAGE", "local")

app = FastAPI(
    title="Nine Chronicles Season Pass Service",
    description="",
    version=__VERSION__,
    debug=settings.DEBUG,
)


@app.middleware("http")
def log_incoming_url(request: Request, call_next):
    logger.info(f"[{request.method}] {request.url}")
    return call_next(request)


@app.exception_handler(Exception)
def handle_exceptions(e: Exception):
    logger.error(e)
    if type(e) in (SeasonNotFoundError, UserNotFoundError):
        status_code = HTTP_404_NOT_FOUND
    elif type(e) in (InvalidSeasonError, NotPremiumError):
        status_code = HTTP_400_BAD_REQUEST
    elif type(e) == ServerOverloadError:
        status_code = HTTP_503_SERVICE_UNAVAILABLE
    elif type(e) == ReadTimeout:
        status_code = HTTP_204_NO_CONTENT
    else:
        status_code = HTTP_500_INTERNAL_SERVER_ERROR

    return JSONResponse(status_code=status_code, content=e)


@app.get("/ping", tags=["Default"])
def ping():
    return "pong"


@app.get(
    "/robots.txt",
    response_class=FileResponse,
    tags=["Default"],
    summary="Return robots.txt",
)
def robots():
    return "season_pass/robots.txt"


app.include_router(api.router)
# app.mount("/_app", StaticFiles(directory="iap/frontend/build/_app"), name="static")

handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run("season_pass.main:app", reload=settings.DEBUG)
