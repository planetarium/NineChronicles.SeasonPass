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

from worker.consts import HOST_LIST
from worker.schemas.action import ActionJson
from worker.utils.stake import StakeAPCoef


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
    query = gql(f"""
    subscription {{
        tx (actionType: "{reg}") {{
            txResult {{txStatus, blockIndex}}
            transaction {{
                signer 
                actions {{json}}
            }}
        }}
    }}
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
