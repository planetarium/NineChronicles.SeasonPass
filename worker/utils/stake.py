from collections import defaultdict

import requests


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
