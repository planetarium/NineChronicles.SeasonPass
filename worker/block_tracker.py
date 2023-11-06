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
from schemas.action import ActionJson
from utils.stake import StakeAPCoef

EXEC_LIMIT = 70  # Finish subscribe after 70 sec. : about 1min + 1block


def get_deposit(coef: StakeAPCoef, url: str, result: dict, addr: str):
    query = '{{stateQuery {{stakeState(address: "{addr}") {{deposit}}}}}}'.format(addr=addr)
    resp = requests.post(url, json={"query": query})
    data = resp.json()["data"]["stateQuery"]["stakeState"]
    if data is None:
        stake_amount = 0
    else:
        stake_amount = float(data["deposit"])

    result[addr] = coef.get_ap_coef(stake_amount)


def send_message(tip: int, action_data: defaultdict, stake_data: defaultdict):
    sqs = boto3.client("sqs", region_name=os.environ.get("REGION_NAME"))
    message = {
        "block": tip,
        "action_data": dict(action_data),
        "stake": dict(stake_data),
    }
    resp = sqs.send_message(
        QueueUrl=os.environ.get("SQS_URL"),
        MessageBody=json.dumps(message),
    )
    logging.info(f"Message {resp['MessageId']} sent to SQS for block {tip}.")


def subscribe_tip(url: str, thread_dict: defaultdict, stake_data: defaultdict, action_data: defaultdict):
    query = gql(""" subscription { tipChanged { index } } """)
    transport = WebsocketsTransport(url=url.replace("https", "wss"))
    client = Client(transport=transport, fetch_schema_from_transport=True)
    for result in client.subscribe(query):
        tip = result["tipChanged"]["index"] - 1
        for t in thread_dict[tip]:
            t.join()
        logging.info(f"{len(action_data[tip])} actions sent to queue for block {tip}")
        logging.info(f"{len(stake_data[tip])} deposits fetched.")
        logging.debug(stake_data)

        send_message(tip, action_data[tip], stake_data[tip])

        # Clear finished
        del thread_dict[tip]
        del action_data[tip]
        del stake_data[tip]


def subscribe_action(url: str, thread_dict: defaultdict, stake_data: defaultdict, action_data: defaultdict):
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
    regex = re.compile(reg)

    for result in client.subscribe(query):
        block_index = result["tx"]["txResult"]["blockIndex"]
        # Save action data and get NCG stake amount for later
        if result["tx"]["txResult"]["txStatus"] == "SUCCESS":
            signer = result["tx"]["transaction"]["signer"]
            logging.debug(f"Action from {signer}")
            # FIXME: Call thread only when `"sweep" in action_json.type_id`
            t = Thread(target=get_deposit, args=(coef, url, stake_data, signer))
            thread_dict[block_index].append(t)
            t.start()
            for action in result["tx"]["transaction"]["actions"]:
                action_raw = json.loads(action["json"].replace(r"\uFEFF", ""))
                type_id = action_raw["type_id"]
                action_json = ActionJson(type_id=type_id, **(action_raw["values"]))
                if regex.match(action_json.type_id):
                    action_data[block_index][action_json.type_id].append({
                        "agent_addr": signer,
                        "avatar_addr": action_json.avatar_addr,
                        "count_base": action_json.count_base,
                    })


def handle(event, context):
    stage = os.environ.get("STAGE", "development")
    url = f"{random.choice(HOST_LIST[stage])}/graphql"

    # Init
    thread_dict = defaultdict(list)
    stake_data = defaultdict(lambda: defaultdict(float))
    action_data = defaultdict(lambda: defaultdict(list))

    # Subscribe Tx. Forever
    tip_thread = Thread(target=subscribe_tip, args=(url, thread_dict, stake_data, action_data))
    action_thread = Thread(target=subscribe_action, args=(url, thread_dict, stake_data, action_data))
    tip_thread.start()
    action_thread.start()
    tip_thread.join(timeout=EXEC_LIMIT)
    action_thread.join(timeout=EXEC_LIMIT)

    for block in action_data.keys():
        for t in thread_dict[block]:
            t.join()
        send_message(block, action_data[block], stake_data[block])


if __name__ == "__main__":
    handle(None, None)
