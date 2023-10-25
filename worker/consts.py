import os

HOST_LIST = {
    "development": [
        os.environ.get("HEADLESS", "http://localhost")
    ],
    "internal": [
        "https://9c-internal-rpc-1.nine-chronicles.com",
    ],
    "mainnet": [
        "https://9c-main-full-state.nine-chronicles.com",
        "https://9c-main-validator-5.nine-chronicles.com",
    ],
}

ITEM_FUNGIBLE_ID_DICT = {
    "400000": "3991e04dd808dc0bc24b21f5adb7bf1997312f8700daf1334bf34936e8a0813a",
    "500000": "00dfffe23964af9b284d121dae476571b7836b8d9e2e5f510d92a840fecc64fe",
    "600201": "f8faf92c9c0d0e8e06694361ea87bfc8b29a8ae8de93044b98470a57636ed0e0",
}
