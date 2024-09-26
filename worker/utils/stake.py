from collections import defaultdict

import requests

from common.utils.season_pass import create_jwt_token


class StakeAPCoef:
    def __init__(self, gql_url: str = "", jwt_secret: str = ""):
        self.gql_url = gql_url
        self.jwt_secret = jwt_secret
        self.crit = []

    def __fetch(self):
        self.crit = []
        if not self.gql_url:
            return

        # StakeActionPointCoefficientSheet Address: 0x4ce2d0Bc945c0E38Ae6c31B0dEe7030951eF1cD1
        resp = requests.post(
            self.gql_url,
            json={"query": '''{state(
                             accountAddress: "0x1000000000000000000000000000000000000000",
                             address: "0x4ce2d0Bc945c0E38Ae6c31B0dEe7030951eF1cD1"
                             )}'''},
            headers={"Authorization": f"Bearer {create_jwt_token(self.jwt_secret)}"}
        )
        state = resp.json()["data"]["state"]
        raw = bytes.fromhex(state)
        self.data = raw.decode().split(":")[1]
        # See `StakeActionPointCoefficientSheet.csv` sheet in lib9c
        head, *body = [x.split(",") for x in self.data.split("\n")]
        d = defaultdict(int)  # Pair of (stake amount : AP coefficient)
        for b in body:
            d[int(b[1])] = int(b[-1])

        _min = 0
        _coef = 100
        for val, coef in sorted(list(d.items())):
            if coef < _coef:
                self.crit.append([range(_min, val), _coef])
                _min = val
                _coef = coef
        self.crit.append([range(_min, 2 ** 64), _coef])

    def set_url(self, *, gql_url: str):
        self.gql_url = gql_url
        self.__fetch()

    def get_ap_coef(self, val) -> int:
        for rng, coef in self.crit:
            if val in rng:
                return coef
        return 100  # Default
