import logging
import os

import uvicorn
from fastapi import FastAPI
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

from app import api
from app.config import config
from app.exceptions import (
    InvalidSeasonError,
    NotPremiumError,
    SeasonNotFoundError,
    ServerOverloadError,
    UserNotFoundError,
)

__VERSION__ = "0.3.1"

stage = config.stage

app = FastAPI(
    title="Nine Chronicles Season Pass Service",
    description="",
    version=__VERSION__,
    debug=config.debug,
)


@app.middleware("http")
def log_incoming_url(request: Request, call_next):
    logging.info(f"[{request.method}] {request.url}")
    return call_next(request)


@app.exception_handler(Exception)
def handle_exceptions(e: Exception):
    logging.error(e)
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


if __name__ == "__main__":
    # 기본값 설정
    workers = config.workers
    timeout_keep_alive = config.timeout_keep_alive
    host = config.host
    port = config.port

    # uvicorn 서버 실행
    uvicorn.run(
        "main:app",
        reload=config.debug,
        host=host,
        port=port,
        workers=workers,
        timeout_keep_alive=timeout_keep_alive,
    )
