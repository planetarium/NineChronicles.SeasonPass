import datetime
import logging
import os
from typing import Union, Dict, Any, Tuple, Optional, List

from gql import Client
from gql.dsl import DSLSchema, dsl_gql, DSLQuery, DSLMutation
from gql.transport.requests import RequestsHTTPTransport
from graphql import DocumentNode, ExecutionResult

from common.enums import PlanetID


class GQL:
    def __init__(self):
        self._url = {
            PlanetID.ODIN: os.environ.get("ODIN_GQL_URL"),
            PlanetID.HEIMDALL: os.environ.get("HEIMDALL_GQL_URL"),
            PlanetID.ODIN_INTERNAL: os.environ.get("ODIN_INTERNAL_GQL_URL"),
            PlanetID.HEIMDALL_INTERNAL: os.environ.get("HEIMDALL_INTERNAL_GQL_URL"),
        }
        self.client = None
        self.ds = None

    def reset(self, planet_id: PlanetID):
        transport = RequestsHTTPTransport(url=self._url[planet_id], verify=True, retries=2)
        self.client = Client(transport=transport, fetch_schema_from_transport=True)
        with self.client as _:
            assert self.client.schema is not None
            self.ds = DSLSchema(self.client.schema)

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
        ts = kwargs.get("timestamp", (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat())
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
        ts = kwargs.get("timestamp", (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat())
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
                        claimData=[{
                            "avatarAddress": avatar_addr,
                            "fungibleAssetValues": [{"ticker": x["ticker"], "quantity": x["amount"],
                                                     "decimalPlaces": x.get("decimal_places", 0), "minters": []}
                                                    for x in claim_data],
                        }],
                        memo=memo
                    )
                )
            )
        )
        result = self.execute(query)
        return bytes.fromhex(result["actionTxQuery"]["claimItems"])

    def create_action(self, planet_id: PlanetID, action_type: str, pubkey: bytes, nonce: int, **kwargs) -> bytes:
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
                        unsignedTransaction=unsigned_tx.hex(),
                        signature=signature.hex()
                    )
                )
            )
        )
        result = self.execute(query)
        return bytes.fromhex(result["transaction"]["signTransaction"])

    def stage(self, planet_id: PlanetID, signed_tx: bytes) -> Tuple[bool, str, Optional[str]]:
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
