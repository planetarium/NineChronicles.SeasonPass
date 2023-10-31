# This module must persist forever.
# Run this module on server, not on lambda

import json
import logging
import os
import random
import re
from collections import defaultdict
from threading import Thread

import boto3
import requests
from gql import Client, gql
from gql.transport.websockets import WebsocketsTransport

from consts import HOST_LIST
from worker.schemas.action import ActionJson


class StakeAPCoef:
    def __init__(self, gql_url):
        self.gql_url = gql_url
        # StakeActionPointCoefficientSheet Address: 0x4ce2d0Bc945c0E38Ae6c31B0dEe7030951eF1cD1
        resp = requests.post(self.gql_url,
                             json={"query": '{state(address: "0x4ce2d0Bc945c0E38Ae6c31B0dEe7030951eF1cD1")}'})
        state = resp.json()["data"]["state"]
        raw = bytes.fromhex(state)
        self.data = raw.decode().split(":")[1]
        head, *body = [x.split(",") for x in self.data.split("\n")]
        d = defaultdict(int)
        for b in body:
            d[int(b[-1])] = int(b[1])

        self.crit = []
        _min = 0
        for coef, val in d.items():
            self.crit.append([range(_min, val), coef])
            _min = val
        self.crit.append([range(_min, 2 ** 64), coef])

    def get_ap_coef(self, val):
        for rng, coef in self.crit:
            if val in rng:
                return coef


def get_deposit(coef: StakeAPCoef, url: str, result: dict, addr: str):
    query = '{{stateQuery {{stakeState(address: "{addr}") {{deposit}}}}}}'.format(addr=addr)
    resp = requests.post(url, json={"query": query})
    data = resp.json()["data"]["stateQuery"]["stakeState"]
    if data is None:
        stake_amount = 0
    else:
        stake_amount = float(data["deposit"])

    result[addr] = coef.get_ap_coef(stake_amount)


def handler():
    stage = os.environ.get("STAGE", "development")
    url = f"{random.choice(HOST_LIST[stage])}/graphql"
    transport = WebsocketsTransport(url=url.replace("https", "wss"))
    client = Client(transport=transport, fetch_schema_from_transport=True)
    coef = StakeAPCoef(url)

    # FIXME: Change actionType to regex: (hack_and_slash.*)|(battle_arena.*)|(raid.*)
    reg = r"(hack_and_slash.*)|(battle_arena.*)|(raid.*)"
    query = gql("""
    subscription {
        tx (actionType: "hack_and_slash21") {
            txResult {txStatus, blockIndex}
            transaction {
                signer 
                actions {json}
            }
        }
    }
    """)

    # Init
    tip = None
    thread_list = []
    action_data = defaultdict(list)
    stake_data = defaultdict(float)
    regex = re.compile(reg)
    sqs = boto3.client("sqs", region_name=os.environ.get("REGION_NAME"))

    # Subscribe Tx. Forever
    for result in client.subscribe(query):
        block_index = result["tx"]["txResult"]["blockIndex"]
        if tip is None:
            tip = block_index
        elif block_index != tip:
            for t in thread_list:
                t.join()
            logging.info(f"{len(action_data)} actions sent to queue for block {tip}")
            logging.info(f"{len(stake_data)} deposits fetched.")
            logging.debug(stake_data)

            message = {
                "block": tip,
                "action_data": dict(action_data),
                "stake": dict(stake_data),
            }
            resp = sqs.send_message(
                QueueUrl=os.environ.get("SQS_URL"),
                MessageBody=json.dumps(message),
            )
            logging.info(f"Message {resp['MessageId']} sent do SQS.")

            # Clear
            action_data = defaultdict(list)
            thread_list = []
            stake_data = defaultdict(float)
            tip = block_index

        # Save action data and get NCG stake amount for later
        if result["tx"]["txResult"]["txStatus"] == "SUCCESS":
            signer = result["tx"]["transaction"]["signer"]
            logging.debug(f"Action from {signer}")
            # FIXME: Call thread only when `"sweep" in action_json.type_id`
            t = Thread(target=get_deposit, args=(coef, url, stake_data, signer))
            thread_list.append(t)
            t.start()
            for action in result["tx"]["transaction"]["actions"]:
                action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
                type_id = action_raw["type_id"]
                action_json = ActionJson(type_id=type_id, **(action_raw["values"]))
                if regex.match(action_json.type_id):
                    action_data[action_json.type_id].append({
                        "agent_addr": signer,
                        "avatar_addr": action_json.avatar_addr,
                        "count_base": action_json.count_base,
                    })


if __name__ == "__main__":
    handler()
