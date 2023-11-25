import os

import requests
import uvicorn
from fastapi import FastAPI, Depends
from mangum import Mangum
from sqlalchemy import func
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.status import HTTP_404_NOT_FOUND

from common import logger
from common.models.action import Block
from season_pass import settings, api
from season_pass.dependencies import session
from season_pass.exceptions import SeasonNotFoundError, UserNotFoundError

__VERSION__ = "0.2.0"

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


@app.get("/block-status")
def block_status(sess=Depends(session)):
    resp = requests.post(
        os.environ["ODIN_VALIDATOR_URL"],
        json={"query": "{ nodeStatus { tip { index } } }"}
    )
    odin_tip = resp.json()["data"]["nodeStatus"]["tip"]["index"]
    resp = requests.post(
        os.environ["HEIMDALL_VALIDATOR_URL"],
        json={"query": "{ nodeStatus { tip { index } } }"}
    )
    heimdall_tip = resp.json()["data"]["nodeStatus"]["tip"]["index"]

    latest = (sess.query(Block.planet_id, func.max(Block.index))
              .group_by(Block.planet_id).order_by(Block.planet_id)
              ).all()

    err = abs(latest[0][1] - odin_tip) > 10 or abs(latest[1][1] - heimdall_tip) > 10
    msg = {
        latest[0][0].decode(): {
            "tip": odin_tip,
            "latest": latest[0][1],
            "diverge": abs(odin_tip - latest[0][1]),
        },
        latest[1][0].decode(): {
            "tip": heimdall_tip,
            "latest": latest[1][1],
            "diverge": abs(heimdall_tip - latest[1][1]),
        },
    }
    return JSONResponse(status_code=503 if err else 200, content=msg, )


app.include_router(api.router)
# app.mount("/_app", StaticFiles(directory="iap/frontend/build/_app"), name="static")

handler = Mangum(app)

if __name__ == "__main__":
    uvicorn.run("main:app", reload=settings.DEBUG)
