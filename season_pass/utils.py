import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Header, HTTPException
from jwt import ExpiredSignatureError

from season_pass import settings


def verify_token(authorization: Annotated[str, Header()]):
    """
    Verify required API must use this.
    This function verifies `Authorization` Bearer JWT type header.

    ### How to creat token
    1. Create token data
        ```
        now = datetime.now(tz=timezone.utc)
        data = {
            "iat": now,
            "exp": now + timedelta(hours=1),  # Header with longer lifetime than 1 hour is refused.
            "aud": "SeasonPass"  # Fixed value
        }
        ```
    2. Create JWT with given secret key
        ```
        token_secret = os.environ.get("JWT_TOKEN_SECRET")
        token = jwt.encode(data, token_secrete, algorithm="HS256")
        ```

    3. Use JWT as Bearer Authorization token
        ```
        headers = {
            "Authorization": "Barer {token}".format(token=token)
        }
        requests.post(URL, headers=headers)
        ```
    JWT verification will check these conditions:
    - Token must be encoded with given secret key
    - Token must be encoded using `HS256` algorithm
    - Token `iat` (Issued at) must be past timestamp
    - Token lifetime must be shorter than 1 hour
    - Token `exp` (Expires at) must be future timestamp
    - Token `aud` (Audience) must be `SeasonPass`

    API will return `401 Not Authorized` if any of these check fails.
    """
    now = datetime.now(tz=timezone.utc)
    try:
        prefix, body = authorization.split(" ")
        if prefix != "Bearer":
            raise Exception("Invalid token type. Use `Bearer [TOKEN]`.")
        token_data = jwt.decode(body, settings.JWT_TOKEN_SECRET, audience="SeasonPass", algorithms=["HS256"])
        if ((datetime.fromtimestamp(token_data["iat"], tz=timezone.utc) + timedelta(hours=1))
                < datetime.fromtimestamp(token_data["exp"], tz=timezone.utc)):
            raise ExpiredSignatureError("Too long token lifetime")
        if datetime.fromtimestamp(token_data["iat"], tz=timezone.utc) > now:
            raise ExpiredSignatureError("Invalid token issue timestamp")
        if datetime.fromtimestamp(token_data["exp"], tz=timezone.utc) < now:
            raise ExpiredSignatureError("Token expired")

    except Exception as e:
        logging.warning(e)
        raise HTTPException(status_code=401, detail="Not Authorized")
