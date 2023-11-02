import logging
import sys
from typing import List

from scripts.google import Spreadsheet


def fetch(credential_file: str, sheet_id: str):
    sheet = Spreadsheet(sheet_id, credential_file)
    data = sheet.get_values("Rewardlist!B1:H")
    logging.debug(data)
    return data["values"]


def to_json(data: List):
    reward_list = []
    head, *body = data
    for b in body:
        data = {"level": int(b[0]), "normal": {"item": [], "currency": []}, "premium": {"item": [], "currency": []}}

        if b[1] == "crystal":
            data["normal"]["currency"].append({"ticker": "CRYSTAL", "amount": int(b[2].replace(",", ""))})
        else:
            data["normal"]["item"].append({"id": int(b[1]), "amount": int(b[2].replace(",", ""))})

        if len(b) > 3 and b[3]:
            if b[3] == "crystal":
                data["premium"]["currency"].append({"ticker": "CRYSTAL", "amount": int(b[4].replace(",", ""))})
            else:
                data["premium"]["item"].append({"id": int(b[3]), "amount": int(b[4].replace(",", ""))})

        if len(b) > 5 and b[5]:
            if b[5] == "crystal":
                data["premium"]["currency"].append({"ticker": "CRYSTAL", "amount": int(b[6].replace(",", ""))})
            else:
                data["premium"]["item"].append({"id": int(b[5]), "amount": int(b[6].replace(",", ""))})

        reward_list.append(data)
    return reward_list


def main(credential_file: str, sheet_id: str):
    data = fetch(credential_file, sheet_id)
    reward_list = to_json(data)
    print(reward_list)
    return reward_list


if __name__ == "__main__":
    if len(sys.argv) < 3:
        logging.error("Instruction: python fetch_reward.py [google credential filename] [target google sheet ID]")
        exit(1)

    main(sys.argv[1], sys.argv[2])
