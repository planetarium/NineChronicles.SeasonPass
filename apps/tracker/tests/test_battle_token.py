"""validate_battle_token: arena battle tokens are RS256 JWTs from the arena service; only valid ones count."""
import base64
import datetime

import jwt
import pytest
from app.config import config
from app.trackers import courage_tracker
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PUB_B64 = base64.b64encode(
    KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
).decode()


@pytest.fixture(autouse=True)
def arena_key(monkeypatch):
    monkeypatch.setattr(config, "arena_service_jwt_public_key", PUB_B64)


def token(key=KEY, **claims):
    payload = {
        "iss": "planetarium arena service",
        "aud": "NineChronicles headless",
        "bid": 42,
        "exp": datetime.datetime.now(tz=datetime.timezone.utc)
        + datetime.timedelta(minutes=5),
    }
    payload.update(claims)
    return jwt.encode(
        {k: v for k, v in payload.items() if v is not None}, key, algorithm="RS256"
    )


def test_valid_token_returns_battle_id():
    assert courage_tracker.validate_battle_token(token()) == 42


@pytest.mark.parametrize(
    "bad",
    [
        dict(key=OTHER),  # not signed by the arena service
        dict(iss="someone else"),
        dict(aud="other audience"),
        dict(exp=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)),
        dict(bid=None),  # no battle id
    ],
)
def test_invalid_tokens_are_rejected(bad):
    with pytest.raises(ValueError):
        courage_tracker.validate_battle_token(token(**bad))


def test_garbage_is_rejected():
    with pytest.raises(ValueError):
        courage_tracker.validate_battle_token("not-a-jwt")
