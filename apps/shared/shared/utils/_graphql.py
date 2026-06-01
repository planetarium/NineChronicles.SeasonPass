import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

import jwt
import requests
from gql import Client
from gql.dsl import DSLMutation, DSLQuery, DSLSchema, dsl_gql
from gql.transport.requests import RequestsHTTPTransport
from graphql import DocumentNode, ExecutionResult
from shared.enums import PlanetID

# Per-endpoint GraphQL schema cache. Introspection is expensive and the schema
# is stable per headless URL, so we introspect at most once per URL across the
# process lifetime. See issue #303.
_SCHEMA_CACHE: Dict[str, Any] = {}


class GQLClient:
    def __init__(
        self,
        gql_url_map: Dict[PlanetID, str],
        jwt_secret: str = None,
        timeout: Optional[float] = None,
    ):
        self.gql_url_map = gql_url_map
        self.client = None
        self.ds = None
        self.timeout = timeout
        self.__jwt_secret = jwt_secret

    def __create_token(self) -> str:
        iat = datetime.datetime.now(tz=datetime.timezone.utc)
        return jwt.encode(
            {
                "iat": iat,
                "exp": iat + datetime.timedelta(minutes=1),
                "iss": "planetariumhq.com",
            },
            self.__jwt_secret,
        )

    def __get_ttl(self) -> str:
        return (datetime.datetime.utcnow() + datetime.timedelta(days=3)).isoformat()

    def __create_header(self):
        return {"Authorization": f"Bearer {self.__create_token()}"}

    def reset(self, planet_id: PlanetID):
        url = self.gql_url_map[planet_id]
        transport = RequestsHTTPTransport(
            url=url,
            verify=True,
            retries=2,
            headers=self.__create_header(),
            timeout=self.timeout,
        )

        schema = _SCHEMA_CACHE.get(url)
        if schema is None:
            # One-time introspection per URL, bounded by `timeout` on the transport.
            introspect_client = Client(
                transport=transport, fetch_schema_from_transport=True
            )
            with introspect_client as _:
                assert introspect_client.schema is not None
                schema = introspect_client.schema
            _SCHEMA_CACHE[url] = schema

        self.client = Client(transport=transport, schema=schema)
        self.ds = DSLSchema(schema)

    def execute(self, query: DocumentNode) -> Union[Dict[str, Any], ExecutionResult]:
        with self.client as sess:
            return sess.execute(query)

    def get_next_nonce(self, planet_id: PlanetID, address: str) -> int:
        """
        Get next Tx Nonce to create Transaction.
        -1 will be returned in case of any error.

        :param planet_id: Planet ID to send GQL query.
        :param str address: 9c Address to get next Nonce.
        :return: Next tx Nonce. In case of any error, `-1` will be returned.
        """
        self.reset(planet_id)
        query = dsl_gql(
            DSLQuery(
                self.ds.StandaloneQuery.transaction.select(
                    self.ds.TransactionHeadlessQuery.nextTxNonce.args(
                        address=address,
                    )
                )
            )
        )
        resp = self.execute(query)

        if "errors" in resp:
            logging.error(f"GQL failed to get next Nonce: {resp['errors']}")
            return -1

        return resp["transaction"]["nextTxNonce"]

    def _unload_from_garage(self, pubkey: bytes, nonce: int, **kwargs) -> bytes:
        ts = kwargs.get("timestamp", self.__get_ttl())
        fav_data = kwargs.get("fav_data")
        avatar_addr = kwargs.get("avatar_addr")
        item_data = kwargs.get("item_data")
        memo = kwargs.get("memo", "")

        if not fav_data and not item_data:
            raise ValueError("Nothing to unload")

        query = dsl_gql(
            DSLQuery(
                self.ds.StandaloneQuery.actionTxQuery.args(
                    publicKey=pubkey.hex(),
                    nonce=nonce,
                    timestamp=ts,
                ).select(
                    self.ds.ActionTxQuery.unloadFromMyGarages.args(
                        recipientAvatarAddr=avatar_addr,
                        fungibleAssetValues=fav_data,
                        fungibleIdAndCounts=item_data,
                        memo=memo,
                    )
                )
            )
        )
        result = self.execute(query)
        return bytes.fromhex(result["actionTxQuery"]["unloadFromMyGarages"])

    def _claim_items(self, pubkey: bytes, nonce: int, **kwargs) -> bytes:
        ts = kwargs.get("timestamp", self.__get_ttl())
        avatar_addr: str = kwargs.get("avatar_addr")
        claim_data: List[Dict[str, Any]] = kwargs.get("claim_data")
        memo = kwargs.get("memo")

        if not claim_data:
            raise ValueError("Nothing to claim")

        query = dsl_gql(
            DSLQuery(
                self.ds.StandaloneQuery.actionTxQuery.args(
                    publicKey=pubkey.hex(),
                    nonce=nonce,
                    timestamp=ts,
                ).select(
                    self.ds.ActionTxQuery.claimItems.args(
                        claimData=[
                            {
                                "avatarAddress": avatar_addr,
                                "fungibleAssetValues": [
                                    {
                                        "ticker": x["ticker"],
                                        "quantity": x["amount"],
                                        "decimalPlaces": x.get("decimal_places", 0),
                                        "minters": [],
                                    }
                                    for x in claim_data
                                ],
                            }
                        ],
                        memo=memo,
                    )
                )
            )
        )
        result = self.execute(query)
        return bytes.fromhex(result["actionTxQuery"]["claimItems"])

    def create_action(
        self, planet_id: PlanetID, action_type: str, pubkey: bytes, nonce: int, **kwargs
    ) -> bytes:
        self.reset(planet_id)
        fn = getattr(self, f"_{action_type}")
        if not fn:
            raise ValueError(f"Action named {action_type} does not exists.")

        return fn(pubkey, nonce, **kwargs)

    def sign(self, planet_id: PlanetID, unsigned_tx: bytes, signature: bytes) -> bytes:
        self.reset(planet_id)
        query = dsl_gql(
            DSLQuery(
                self.ds.StandaloneQuery.transaction.select(
                    self.ds.TransactionHeadlessQuery.signTransaction.args(
                        unsignedTransaction=unsigned_tx.hex(), signature=signature.hex()
                    )
                )
            )
        )
        result = self.execute(query)
        return bytes.fromhex(result["transaction"]["signTransaction"])

    def stage(
        self, planet_id: PlanetID, signed_tx: bytes
    ) -> Tuple[bool, str, Optional[str]]:
        self.reset(planet_id)
        query = dsl_gql(
            DSLMutation(
                self.ds.StandaloneMutation.stageTransaction.args(
                    payload=signed_tx.hex()
                )
            )
        )
        result = self.execute(query)
        if "errors" in result:
            return False, result["errors"][0]["message"], None
        return True, "", result["stageTransaction"]

    def get_last_cleared_stage(
        self, planet_id: PlanetID, avatar_addr: str, timeout: int = None
    ) -> Tuple[int, int]:
        if timeout is None:
            timeout = self.timeout
        query = f"""{{ stateQuery {{ avatar(avatarAddress: "{avatar_addr}") {{
        worldInformation {{ lastClearedStage {{ worldId stageId }} }}
        }} }} }}"""
        resp = requests.post(
            self.gql_url_map[planet_id],
            json={"query": query},
            headers={"Authorization": f"Bearer {self.__create_token()}"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return 0, 0
        try:
            result = resp.json()["data"]["stateQuery"]["avatar"]["worldInformation"][
                "lastClearedStage"
            ]
        except Exception as e:
            return 0, 0
        else:
            return result["worldId"], result["stageId"]
