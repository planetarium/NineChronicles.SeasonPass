from datetime import datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Header, HTTPException
from jwt import ExpiredSignatureError

from season_pass import settings


def verify_token(authorization: Annotated[str, Header()]):
    try:
        prefix, body = authorization.split(" ")
        if prefix != "Bearer":
            raise Exception()
        token_data = jwt.decode(body, settings.JWT_TOKEN_SECRET, audience="SeasonPass", algorithms=["HS256"])
        if (datetime.utcfromtimestamp(token_data["iat"]) + timedelta(hours=1)) < datetime.utcfromtimestamp(
                token_data["exp"]):
            raise ExpiredSignatureError()
    except Exception:
        raise HTTPException(status_code=401, detail="Not Authorized")
