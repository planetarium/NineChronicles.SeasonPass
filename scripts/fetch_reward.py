import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import List, Tuple

from scripts.google import Spreadsheet

FAV_DICT = {
    "CRYSTAL": 18,
    "RUNE_GOLDENLEAF": 0,
    "RUNESTONE_GOLDENTHOR": 0,
    "RUNESTONE_CRI": 0,
    "RUNESTONE_HP": 0,
}


def fetch(credential_file: str, sheet_id: str):
    sheet = Spreadsheet(sheet_id, credential_file)
    data = sheet.get_values("RewardList!A1:H")
    logging.debug(data)
    return data["values"]


def to_ticker(item_id: str | int) -> Tuple[str, int]:
    """
    Create ticker from item ID and returns with decimal places.
    """
    if item_id.upper() in FAV_DICT:
        return f"FAV__{item_id.upper()}", FAV_DICT.get(item_id.upper(), 0)
    else:
        return f"Item_NT_{item_id}", 0


def restruct(data: List) -> dict:
    reward_dict = defaultdict(list)
    head, *body = data
    for b in body:
        data = {"level": int(b[1]), "normal": [], "premium": []}
        ticker, decimal_places = to_ticker(b[2])
        data["normal"].append(
            {"ticker": ticker, "amount": int(b[3].replace(",", "")), "decimal_places": decimal_places})

        if len(b) > 4 and b[4]:
            ticker, decimal_places = to_ticker(b[4])
            data["premium"].append(
                {"ticker": ticker, "amount": int(b[5].replace(",", "")), "decimal_places": decimal_places})

        if len(b) > 6 and b[6]:
            ticker, decimal_places = to_ticker(b[6])
            data["premium"].append(
                {"ticker": ticker, "amount": int(b[7].replace(",", "")), "decimal_places": decimal_places})

        reward_dict[b[0]].append(data)
    return reward_dict


def main(credential_file: str, sheet_id: str):
    if not os.path.exists("data"):
        os.mkdir("data")
    data = fetch(credential_file, sheet_id)
    reward_dict = restruct(data)
    for pass_type, reward_list in reward_dict.items():
        print(pass_type)
        print("-"*24)
        print(json.dumps(reward_list))
        print("="*48)
        with open(f"data/{datetime.now().strftime('%Y-%m-%d_%H-%M')}_{pass_type.lower()}_reward.json", "w") as f:
            f.write(json.dumps(reward_list))
    return reward_dict


if __name__ == "__main__":
    if len(sys.argv) < 3:
        logging.error("Instruction: python fetch_reward.py [google credential filename] [target google sheet ID]")
        exit(1)

    main(sys.argv[1], sys.argv[2])
