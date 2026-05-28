from unittest.mock import MagicMock, patch

import pytest
from shared.enums import PlanetID
from shared.utils import _graphql
from shared.utils._graphql import GQLClient

ODIN = PlanetID.ODIN
URL_MAP = {ODIN: "https://odin-rpc.nine-chronicles.com/graphql"}


@pytest.fixture(autouse=True)
def clear_schema_cache():
    # Ensure module-level cache does not leak between tests.
    _graphql._SCHEMA_CACHE.clear()
    yield
    _graphql._SCHEMA_CACHE.clear()


def test_reset_passes_timeout_to_transport():
    client = GQLClient(URL_MAP, jwt_secret="secret", timeout=3.5)
    with patch.object(_graphql, "RequestsHTTPTransport") as MockTransport, patch.object(
        _graphql, "Client"
    ) as MockClient, patch.object(_graphql, "DSLSchema"):
        # The introspection Client's `with` block must yield a schema.
        instance = MockClient.return_value
        instance.__enter__.return_value = instance
        instance.schema = MagicMock()

        client.reset(ODIN)

    _, kwargs = MockTransport.call_args
    assert kwargs["timeout"] == 3.5
    assert kwargs["url"] == URL_MAP[ODIN]


def test_reset_introspects_schema_only_once_per_url():
    client = GQLClient(URL_MAP, jwt_secret="secret", timeout=3.5)
    with patch.object(_graphql, "RequestsHTTPTransport"), patch.object(
        _graphql, "Client"
    ) as MockClient, patch.object(_graphql, "DSLSchema"):
        instance = MockClient.return_value
        instance.__enter__.return_value = instance
        instance.schema = MagicMock()

        client.reset(ODIN)
        introspection_clients_after_first = MockClient.call_count

        client.reset(ODIN)
        introspection_clients_after_second = MockClient.call_count

    # First reset: 1 introspection Client + 1 execution Client = 2 calls.
    # Second reset: schema cached -> only 1 execution Client = +1 call (total 3),
    # NOT another introspection.
    assert introspection_clients_after_first == 2
    assert introspection_clients_after_second == 3


def test_get_last_cleared_stage_defaults_to_instance_timeout():
    client = GQLClient(URL_MAP, jwt_secret="secret", timeout=4.0)
    with patch.object(_graphql, "requests") as mock_requests:
        mock_requests.post.return_value.status_code = 500  # short-circuit parse
        client.get_last_cleared_stage(ODIN, "0xavatar")

    _, kwargs = mock_requests.post.call_args
    assert kwargs["timeout"] == 4.0


def test_get_last_cleared_stage_explicit_timeout_wins():
    client = GQLClient(URL_MAP, jwt_secret="secret", timeout=4.0)
    with patch.object(_graphql, "requests") as mock_requests:
        mock_requests.post.return_value.status_code = 500
        client.get_last_cleared_stage(ODIN, "0xavatar", timeout=1)

    _, kwargs = mock_requests.post.call_args
    assert kwargs["timeout"] == 1
