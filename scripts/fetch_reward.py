import json
import logging
import os
import sys
from datetime import datetime
from typing import List, Tuple

from scripts.google import Spreadsheet


def fetch(credential_file: str, sheet_id: str):
    sheet = Spreadsheet(sheet_id, credential_file)
    data = sheet.get_values("Rewardlist!B1:H")
    logging.debug(data)
    return data["values"]


def to_ticker(item_id: str | int) -> Tuple[str, int]:
    """
    Create ticker from item ID and returns with decimal places.
    """
    DECIMAL_DICT = {
        "CRYSTAL": 18,
        "RUNE_GOLDENLEAF": 0,
    }
    if item_id.upper() in ("CRYSTAL", "RUNE_GOLDENLEAF"):
        return f"FAV__{item_id.upper()}", DECIMAL_DICT.get(item_id.upper(), 0)
    else:
        return f"Item_NT_{item_id}", 0


def to_json(data: List) -> str:
    reward_list = []
    head, *body = data
    for b in body:
        data = {"level": int(b[0]), "normal": [], "premium": []}
        ticker, decimal_places = to_ticker(b[1])
        data["normal"].append(
            {"ticker": ticker, "amount": int(b[2].replace(",", "")), "decimal_places": decimal_places})

        if len(b) > 3 and b[3]:
            ticker, decimal_places = to_ticker(b[3])
            data["premium"].append(
                {"ticker": ticker, "amount": int(b[4].replace(",", "")), "decimal_places": decimal_places})

        if len(b) > 5 and b[5]:
            ticker, decimal_places = to_ticker(b[5])
            data["premium"].append(
                {"ticker": ticker, "amount": int(b[6].replace(",", "")), "decimal_places": decimal_places})

        reward_list.append(data)
    return json.dumps(reward_list)


def main(credential_file: str, sheet_id: str):
    if not os.path.exists("data"):
        os.mkdir("data")
    data = fetch(credential_file, sheet_id)
    reward_list = to_json(data)
    print(reward_list)
    with open(f"data/season_pass_reward_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json", "w") as f:
        f.write(reward_list)
    return reward_list


if __name__ == "__main__":
    if len(sys.argv) < 3:
        logging.error("Instruction: python fetch_reward.py [google credential filename] [target google sheet ID]")
        exit(1)

    main(sys.argv[1], sys.argv[2])
